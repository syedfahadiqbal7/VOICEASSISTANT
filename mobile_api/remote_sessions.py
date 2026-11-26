"""Short-lived, device-bound remote session tickets.

The store is intentionally process-local: restart invalidates every challenge and
ticket. A desktop owner must explicitly create a challenge; mobile redemption
requires an Ed25519 proof from the key already enrolled in the device registry.
Tickets are returned once and only their SHA-256 digests are retained.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mobile_api.signaling import canonical_json

_DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_CHALLENGE_TTL_SECONDS = 90
_MIN_CHALLENGE_TTL_SECONDS = 30
_MAX_CHALLENGE_TTL_SECONDS = 180
_DEFAULT_SESSION_TTL_SECONDS = 600
_MIN_SESSION_TTL_SECONDS = 60
_MAX_SESSION_TTL_SECONDS = 900
_MAX_PENDING_CHALLENGES = 256
_MAX_ACTIVE_SESSIONS = 256
_MAX_PROOF_CLOCK_SKEW_MS = 30_000


class RemoteSessionError(ValueError):
    """A remote-session challenge or ticket is invalid, expired or unauthorized."""


def _b64url_decode(value: Any, expected_length: int) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != (expected_length * 8 + 5) // 6
        or not _B64URL.fullmatch(value)
    ):
        raise RemoteSessionError("Invalid remote-session proof encoding")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as error:
        raise RemoteSessionError("Invalid remote-session proof encoding") from error
    if len(decoded) != expected_length:
        raise RemoteSessionError("Invalid remote-session proof length")
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise RemoteSessionError("Non-canonical remote-session proof encoding")
    return decoded


class RemoteSessionStore:
    """Bounded in-memory challenge/ticket registry bound to enrolled device keys."""

    def __init__(self, *, max_challenges: int = _MAX_PENDING_CHALLENGES,
                 max_sessions: int = _MAX_ACTIVE_SESSIONS):
        if type(max_challenges) is not int or not 1 <= max_challenges <= 4096:
            raise ValueError("max_challenges must be between 1 and 4096")
        if type(max_sessions) is not int or not 1 <= max_sessions <= 4096:
            raise ValueError("max_sessions must be between 1 and 4096")
        self._max_challenges = max_challenges
        self._max_sessions = max_sessions
        self._challenges: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _now_ms(now_ms: int | None) -> int:
        value = int(time.time() * 1000) if now_ms is None else now_ms
        if type(value) is not int or value < 0:
            raise RemoteSessionError("Invalid remote-session clock")
        return value

    def _prune(self, now_ms: int) -> None:
        expired_challenges = [
            key for key, value in self._challenges.items()
            if value["expires_at_ms"] <= now_ms
        ]
        for key in expired_challenges:
            self._challenges.pop(key, None)
        expired_sessions = [
            key for key, value in self._sessions.items()
            if value["expires_at_ms"] <= now_ms
        ]
        for key in expired_sessions:
            self._sessions.pop(key, None)

    def create_challenge(self, device_id: str, registry, *,
                         challenge_ttl_seconds: int = _DEFAULT_CHALLENGE_TTL_SECONDS,
                         session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
                         now_ms: int | None = None) -> dict[str, Any]:
        """Create a challenge after the desktop owner has approved this device."""
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise RemoteSessionError("Invalid paired device ID")
        if type(challenge_ttl_seconds) is not int or not (
            _MIN_CHALLENGE_TTL_SECONDS <= challenge_ttl_seconds <= _MAX_CHALLENGE_TTL_SECONDS
        ):
            raise RemoteSessionError("Challenge lifetime must be between 30 and 180 seconds")
        if type(session_ttl_seconds) is not int or not (
            _MIN_SESSION_TTL_SECONDS <= session_ttl_seconds <= _MAX_SESSION_TTL_SECONDS
        ):
            raise RemoteSessionError("Session lifetime must be between 60 and 900 seconds")
        try:
            identity = registry.get_device_identity(device_id)
        except Exception as error:
            raise RemoteSessionError("Paired-device registry is unavailable") from error
        if not identity or identity.get("algorithm") != "Ed25519":
            raise RemoteSessionError("Device is not actively paired with an Ed25519 identity")

        now = self._now_ms(now_ms)
        with self._lock:
            self._prune(now)
            if len(self._challenges) >= self._max_challenges:
                raise RemoteSessionError("Remote-session challenge capacity reached")
            if len(self._sessions) >= self._max_sessions:
                raise RemoteSessionError("Remote-session capacity reached")
            session_id = uuid.uuid4().hex
            challenge = secrets.token_urlsafe(32)
            expires = now + challenge_ttl_seconds * 1000
            self._challenges[session_id] = {
                "device_id": device_id,
                "challenge_sha256": hashlib.sha256(challenge.encode("ascii")).hexdigest(),
                "identity_fingerprint": identity["fingerprint_sha256"],
                "created_at_ms": now,
                "expires_at_ms": expires,
                "session_ttl_seconds": session_ttl_seconds,
                "attempts": 0,
            }
            return {
                "protocol_version": 1,
                "session_id": session_id,
                "device_id": device_id,
                "challenge": challenge,
                "expires_at_ms": expires,
            }

    def complete_challenge(self, *, session_id: str, device_id: str,
                           challenge: str, issued_at_ms: int,
                           signature: str, registry, now_ms: int | None = None) -> dict[str, Any]:
        """Verify the enrolled mobile key and issue a one-time bearer ticket."""
        if not isinstance(session_id, str) or not _DEVICE_ID.fullmatch(session_id):
            raise RemoteSessionError("Invalid remote-session ID")
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise RemoteSessionError("Invalid paired device ID")
        if (
            not isinstance(challenge, str)
            or not 20 <= len(challenge) <= 128
            or not _B64URL.fullmatch(challenge)
        ):
            raise RemoteSessionError("Invalid remote-session challenge")
        now = self._now_ms(now_ms)
        if type(issued_at_ms) is not int or abs(now - issued_at_ms) > _MAX_PROOF_CLOCK_SKEW_MS:
            raise RemoteSessionError("Remote-session proof timestamp is outside the allowed clock skew")
        with self._lock:
            self._prune(now)
            pending = self._challenges.get(session_id)
            if pending is None:
                raise RemoteSessionError("Remote-session challenge is unknown, expired or already used")
            if pending["device_id"] != device_id:
                raise RemoteSessionError("Remote-session challenge is bound to another device")
            if len(self._sessions) >= self._max_sessions:
                raise RemoteSessionError("Remote-session capacity reached")
            supplied_challenge_hash = hashlib.sha256(challenge.encode("ascii", errors="strict")).hexdigest()
            if not hmac.compare_digest(supplied_challenge_hash, pending["challenge_sha256"]):
                pending["attempts"] += 1
                if pending["attempts"] >= 5:
                    self._challenges.pop(session_id, None)
                raise RemoteSessionError("Remote-session challenge proof does not match")

            try:
                identity = registry.get_device_identity(device_id)
                if not identity or identity.get("algorithm") != "Ed25519":
                    raise RemoteSessionError("Paired device is unknown or revoked")
                if not hmac.compare_digest(
                    str(identity.get("fingerprint_sha256", "")),
                    pending["identity_fingerprint"],
                ):
                    raise RemoteSessionError("Paired device identity changed after challenge creation")
                try:
                    signature_bytes = _b64url_decode(signature, 64)
                except RemoteSessionError as error:
                    pending["attempts"] += 1
                    if pending["attempts"] >= 5:
                        self._challenges.pop(session_id, None)
                    raise RemoteSessionError("Remote-session device signature is invalid") from error
                public_bytes = _b64url_decode(identity["public_key"], 32)
                proof = {
                    "protocol_version": 1,
                    "session_id": session_id,
                    "device_id": device_id,
                    "challenge": challenge,
                    "issued_at_ms": issued_at_ms,
                }
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                    signature_bytes, canonical_json(proof)
                )
            except RemoteSessionError:
                raise
            except (InvalidSignature, ValueError, TypeError, KeyError) as error:
                pending["attempts"] += 1
                if pending["attempts"] >= 5:
                    self._challenges.pop(session_id, None)
                raise RemoteSessionError("Remote-session device signature is invalid") from error
            except Exception as error:
                raise RemoteSessionError("Paired-device registry is unavailable") from error

            # A successful proof consumes the challenge before issuing the ticket.
            self._challenges.pop(session_id, None)
            ticket = secrets.token_urlsafe(32)
            expires = now + pending["session_ttl_seconds"] * 1000
            self._sessions[session_id] = {
                "device_id": device_id,
                "ticket_sha256": hashlib.sha256(ticket.encode("ascii")).hexdigest(),
                "identity_fingerprint": pending["identity_fingerprint"],
                "created_at_ms": now,
                "expires_at_ms": expires,
            }
            return {
                "protocol_version": 1,
                "session_id": session_id,
                "device_id": device_id,
                "access_ticket": ticket,
                "expires_at_ms": expires,
            }

    def authenticate(self, *, session_id: str, device_id: str,
                     access_ticket: str, registry,
                     now_ms: int | None = None) -> bool:
        """Authenticate a ticket and re-check device registration on every use."""
        if (
            not isinstance(session_id, str) or not _DEVICE_ID.fullmatch(session_id)
            or not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id)
            or not isinstance(access_ticket, str) or not 20 <= len(access_ticket) <= 128
        ):
            return False
        now = self._now_ms(now_ms)
        with self._lock:
            self._prune(now)
            session = self._sessions.get(session_id)
            if session is None or session["device_id"] != device_id:
                return False
            supplied_hash = hashlib.sha256(access_ticket.encode("ascii", errors="ignore")).hexdigest()
            if not hmac.compare_digest(supplied_hash, session["ticket_sha256"]):
                return False
            try:
                identity = registry.get_device_identity(device_id)
            except Exception:
                return False
            return bool(
                identity
                and identity.get("algorithm") == "Ed25519"
                and hmac.compare_digest(
                    str(identity.get("fingerprint_sha256", "")),
                    session["identity_fingerprint"],
                )
            )

    def revoke_session(self, session_id: str) -> bool:
        if not isinstance(session_id, str):
            return False
        with self._lock:
            return self._sessions.pop(session_id, None) is not None or self._challenges.pop(session_id, None) is not None

    def revoke_device(self, device_id: str) -> int:
        if not isinstance(device_id, str):
            return 0
        with self._lock:
            keys = [
                key for key, value in self._sessions.items()
                if value["device_id"] == device_id
            ]
            keys.extend(
                key for key, value in self._challenges.items()
                if value["device_id"] == device_id
            )
            for key in keys:
                self._sessions.pop(key, None)
                self._challenges.pop(key, None)
            return len(keys)

    def snapshot(self, *, now_ms: int | None = None) -> dict[str, int]:
        """Return counts only; never expose ticket/challenge material."""
        now = self._now_ms(now_ms)
        with self._lock:
            self._prune(now)
            return {
                "pending_challenges": len(self._challenges),
                "active_sessions": len(self._sessions),
            }
