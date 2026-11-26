"""Canonical, signed envelopes for authenticated remote signaling.

This module is deliberately transport-agnostic. It authenticates signaling
metadata only; it does not authorize commands, approvals, or desktop actions.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROTOCOL_VERSION = 1
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_TTL_MS = 60_000
MAX_CLOCK_SKEW_MS = 10_000
MAX_REPLAY_ENTRIES = 20_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ID = re.compile(r"^[0-9a-f]{32}$")
_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_FIELDS = {
    "protocol_version", "sender_id", "recipient_id", "session_id",
    "message_id", "sequence", "issued_at_ms", "expires_at_ms",
    "message_type", "payload", "signature",
}
_UNSIGNED_FIELDS = _FIELDS - {"signature"}


class SignalingEnvelopeError(ValueError):
    """A signaling envelope failed schema, identity, time, or signature checks."""


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: Any, *, expected_length: int) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != (expected_length * 8 + 5) // 6
        or not _B64URL.fullmatch(value)
    ):
        raise SignalingEnvelopeError("Invalid base64url value")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignalingEnvelopeError("Invalid base64url value") from error
    if len(decoded) != expected_length or _b64url_encode(decoded) != value:
        raise SignalingEnvelopeError("Non-canonical base64url value")
    return decoded


def _validate_json_value(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise SignalingEnvelopeError("Payload nesting exceeds its limit")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value.encode("utf-8")) > 16_384:
            raise SignalingEnvelopeError("Payload string exceeds its limit")
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > MAX_SAFE_INTEGER:
            raise SignalingEnvelopeError("Payload integer exceeds the cross-platform safe range")
        return
    if isinstance(value, float):
        raise SignalingEnvelopeError("Floating-point payload values are not supported")
    if isinstance(value, list):
        if len(value) > 256:
            raise SignalingEnvelopeError("Payload array exceeds its limit")
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(key, str) for key in value):
            raise SignalingEnvelopeError("Payload object is invalid or too large")
        for key, item in value.items():
            if len(key) > 128 or not key.isascii():
                raise SignalingEnvelopeError("Payload keys must be ASCII and within their size limit")
            _validate_json_value(item, depth + 1)
        return
    raise SignalingEnvelopeError("Payload contains a non-JSON value")


def canonical_json(value: dict[str, Any]) -> bytes:
    """Serialize protocol JSON deterministically for Python/Dart signature parity."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise SignalingEnvelopeError("Envelope cannot be canonically encoded") from error


def _validate_unsigned(envelope: dict[str, Any]) -> None:
    if set(envelope) != _UNSIGNED_FIELDS:
        raise SignalingEnvelopeError("Envelope fields do not match protocol version 1")
    if type(envelope["protocol_version"]) is not int or envelope["protocol_version"] != PROTOCOL_VERSION:
        raise SignalingEnvelopeError("Unsupported signaling protocol version")
    for field in ("sender_id", "recipient_id", "session_id", "message_id"):
        if not isinstance(envelope[field], str) or not _ID.fullmatch(envelope[field]):
            raise SignalingEnvelopeError(f"Invalid {field}")
    if type(envelope["sequence"]) is not int or not 1 <= envelope["sequence"] <= MAX_SAFE_INTEGER:
        raise SignalingEnvelopeError("Invalid signaling sequence")
    for field in ("issued_at_ms", "expires_at_ms"):
        if type(envelope[field]) is not int or not 0 <= envelope[field] <= MAX_SAFE_INTEGER:
            raise SignalingEnvelopeError(f"Invalid {field}")
    if not isinstance(envelope["message_type"], str) or not _TYPE.fullmatch(envelope["message_type"]):
        raise SignalingEnvelopeError("Invalid signaling message type")
    if not isinstance(envelope["payload"], dict):
        raise SignalingEnvelopeError("Signaling payload must be an object")
    _validate_json_value(envelope["payload"])
    if len(canonical_json(envelope)) > MAX_ENVELOPE_BYTES:
        raise SignalingEnvelopeError("Signaling envelope exceeds its size limit")


def create_signed_envelope(
    *, sender_id: str, recipient_id: str, session_id: str, sequence: int,
    message_type: str, payload: dict[str, Any], sign: Callable[[bytes], bytes],
    now_ms: int | None = None, ttl_ms: int = 30_000,
) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(ttl_ms) is not int or not 1_000 <= ttl_ms <= MAX_TTL_MS:
        raise SignalingEnvelopeError("Signaling lifetime must be between 1 and 60 seconds")
    unsigned = {
        "protocol_version": PROTOCOL_VERSION,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "session_id": session_id,
        "message_id": uuid.uuid4().hex,
        "sequence": sequence,
        "issued_at_ms": now,
        "expires_at_ms": now + ttl_ms,
        "message_type": message_type,
        "payload": payload,
    }
    _validate_unsigned(unsigned)
    signature = sign(canonical_json(unsigned))
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise SignalingEnvelopeError("Signing provider returned an invalid Ed25519 signature")
    signed = {**unsigned, "signature": _b64url_encode(signature)}
    if len(canonical_json(signed)) > MAX_ENVELOPE_BYTES:
        raise SignalingEnvelopeError("Signaling envelope exceeds its size limit")
    return signed


class ReplayGuard:
    """Bounded in-memory replay protection scoped to session and sender."""

    def __init__(self, *, max_entries: int = MAX_REPLAY_ENTRIES):
        self._max_entries = max(1, min(int(max_entries), MAX_REPLAY_ENTRIES))
        self._lock = threading.RLock()
        self._seen: OrderedDict[tuple[str, str, int], int] = OrderedDict()
        self._message_ids: OrderedDict[tuple[str, str, str], int] = OrderedDict()
        self._highest: dict[tuple[str, str], tuple[int, int]] = {}

    def accept(
        self, session_id: str, sender_id: str, sequence: int,
        message_id: str, expires_at_ms: int, now_ms: int,
    ) -> None:
        key = (session_id, sender_id)
        replay_key = (session_id, sender_id, sequence)
        message_key = (session_id, sender_id, message_id)
        with self._lock:
            expired_sequences = [
                item for item, expiry in self._seen.items() if expiry <= now_ms
            ]
            for item in expired_sequences:
                self._seen.pop(item, None)
            expired_messages = [
                item for item, expiry in self._message_ids.items() if expiry <= now_ms
            ]
            for item in expired_messages:
                self._message_ids.pop(item, None)
            expired_sessions = [
                item for item, (_sequence, expiry) in self._highest.items()
                if expiry <= now_ms
            ]
            for item in expired_sessions:
                self._highest.pop(item, None)

            highest = self._highest.get(key)
            if (
                (highest is not None and sequence <= highest[0])
                or replay_key in self._seen
                or message_key in self._message_ids
            ):
                raise SignalingEnvelopeError("Replayed or out-of-order signaling sequence")
            if (
                len(self._seen) >= self._max_entries
                or len(self._message_ids) >= self._max_entries
                or (highest is None and len(self._highest) >= self._max_entries)
            ):
                raise SignalingEnvelopeError(
                    "Replay-protection capacity reached; refusing new signaling"
                )

            self._seen[replay_key] = expires_at_ms
            self._message_ids[message_key] = expires_at_ms
            self._highest[key] = (sequence, expires_at_ms)


def verify_signed_envelope(
    envelope: Any, *, public_key: str, expected_sender_id: str,
    expected_recipient_id: str, expected_session_id: str,
    replay_guard: ReplayGuard, now_ms: int | None = None,
) -> dict[str, Any]:
    if not isinstance(envelope, dict) or set(envelope) != _FIELDS:
        raise SignalingEnvelopeError("Invalid signaling envelope shape")
    if len(canonical_json(envelope)) > MAX_ENVELOPE_BYTES:
        raise SignalingEnvelopeError("Signaling envelope exceeds its size limit")
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    _validate_unsigned(unsigned)
    if unsigned["sender_id"] != expected_sender_id:
        raise SignalingEnvelopeError("Signaling sender identity is not authorized")
    if unsigned["recipient_id"] != expected_recipient_id:
        raise SignalingEnvelopeError("Signaling recipient does not match this endpoint")
    if unsigned["session_id"] != expected_session_id:
        raise SignalingEnvelopeError("Signaling session does not match")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    issued = unsigned["issued_at_ms"]
    expires = unsigned["expires_at_ms"]
    if expires <= now or issued > now + MAX_CLOCK_SKEW_MS:
        raise SignalingEnvelopeError("Signaling envelope is expired or issued in the future")
    if expires <= issued or expires - issued > MAX_TTL_MS:
        raise SignalingEnvelopeError("Signaling envelope lifetime is invalid")
    if issued < now - MAX_TTL_MS - MAX_CLOCK_SKEW_MS:
        raise SignalingEnvelopeError("Signaling envelope is too old")
    signature = _b64url_decode(envelope["signature"], expected_length=64)
    try:
        raw_key = _b64url_decode(public_key, expected_length=32)
        Ed25519PublicKey.from_public_bytes(raw_key).verify(signature, canonical_json(unsigned))
    except (InvalidSignature, ValueError) as error:
        raise SignalingEnvelopeError("Signaling envelope signature verification failed") from error
    replay_guard.accept(
        unsigned["session_id"], unsigned["sender_id"], unsigned["sequence"],
        unsigned["message_id"], expires, now,
    )
    return unsigned


def verify_mobile_signaling_envelope(
    envelope: Any, *, registry, expected_recipient_id: str,
    expected_session_id: str, replay_guard: ReplayGuard, now_ms: int | None = None,
) -> dict[str, Any]:
    """Resolve the mobile key from the active registry; never trust a key in the message."""
    if (
        not isinstance(envelope, dict)
        or not isinstance(envelope.get("sender_id"), str)
        or not _ID.fullmatch(envelope["sender_id"])
    ):
        raise SignalingEnvelopeError("Invalid signaling sender identity")
    identity = registry.get_device_identity(envelope["sender_id"])
    if identity is None:
        raise SignalingEnvelopeError("Unknown, unpaired, or revoked mobile identity")
    return verify_signed_envelope(
        envelope, public_key=identity["public_key"],
        expected_sender_id=identity["device_id"],
        expected_recipient_id=expected_recipient_id,
        expected_session_id=expected_session_id,
        replay_guard=replay_guard, now_ms=now_ms,
    )


def verify_desktop_signaling_envelope(
    envelope: Any, *, identity_store, expected_recipient_id: str,
    expected_session_id: str, replay_guard: ReplayGuard, now_ms: int | None = None,
) -> dict[str, Any]:
    """Resolve the desktop key from its encrypted local identity store."""
    if not isinstance(envelope, dict):
        raise SignalingEnvelopeError("Invalid signaling envelope")
    identity = identity_store.status()
    if not identity.get("initialized"):
        raise SignalingEnvelopeError("Desktop identity is not initialized")
    return verify_signed_envelope(
        envelope, public_key=identity["public_key"],
        expected_sender_id=identity["identity_id"],
        expected_recipient_id=expected_recipient_id,
        expected_session_id=expected_session_id,
        replay_guard=replay_guard, now_ms=now_ms,
    )
