"""Short-lived, desktop-approved mobile QR pairing sessions.

The QR contains a random one-time challenge, never a device credential. Device
credentials are minted only after explicit desktop approval and are delivered
once to the phone that proves possession of the challenge.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
from typing import Any

from mobile_api.authorization import ROLE_APPROVER, ROLE_OPERATOR, ROLE_VIEWER

_ALLOWED_PAIRING_ROLES = {ROLE_OPERATOR, ROLE_APPROVER, ROLE_VIEWER}
_MAX_SESSIONS = 20
_MAX_ATTEMPTS = 5
_DEFAULT_TTL_SECONDS = 120


class PairingError(ValueError):
    """Pairing request rejected by policy or state."""


class PairingCoordinator:
    """In-memory, bounded, expiring pairing state; restart invalidates all QR codes."""

    def __init__(self, device_registry, *, clock=time.monotonic, max_sessions: int = _MAX_SESSIONS):
        self._registry = device_registry
        self._clock = clock
        self._max_sessions = max(1, min(int(max_sessions), 100))
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _validate_endpoint(endpoint_url: str) -> str:
        if not isinstance(endpoint_url, str):
            raise PairingError("Pairing endpoint must be a URL string")
        value = endpoint_url.strip()
        if len(value) > 2048:
            raise PairingError("Pairing endpoint is too long")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as error:
            raise PairingError("Invalid pairing endpoint") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise PairingError("Pairing requires a trusted HTTPS endpoint without credentials, path, query or fragment")
        return f"https://{parsed.netloc}"

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [key for key, item in self._sessions.items() if item["expires_at"] <= now]
        for key in expired:
            self._sessions.pop(key, None)

    @staticmethod
    def _secret_hash(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def create_session(self, endpoint_url: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> dict[str, Any]:
        endpoint = self._validate_endpoint(endpoint_url)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 30 <= ttl_seconds <= 300:
            raise PairingError("Pairing lifetime must be between 30 and 300 seconds")
        secret = secrets.token_urlsafe(32)
        session_id = uuid.uuid4().hex
        expires_at = self._clock() + ttl_seconds
        expires_utc = datetime.now(timezone.utc).timestamp() + ttl_seconds
        with self._lock:
            self._purge_expired()
            if len(self._sessions) >= self._max_sessions:
                raise PairingError("Too many active pairing sessions; wait for an existing session to expire")
            self._sessions[session_id] = {
                "secret_hash": self._secret_hash(secret),
                "endpoint_url": endpoint,
                "expires_at": expires_at,
                "expires_at_utc": datetime.fromtimestamp(expires_utc, timezone.utc).isoformat(),
                "attempts": 0,
                "request_id": None,
                "device_name": None,
                "state": "waiting",
                "credentials": None,
                "result_consumed": False,
            }
        return {
            "version": 1,
            "type": "voiceassistant_mobile_pairing",
            "endpoint": endpoint,
            "session_id": session_id,
            "secret": secret,
            "expires_at": datetime.fromtimestamp(expires_utc, timezone.utc).isoformat(),
        }

    def _get_session(self, session_id: str, secret: str) -> dict[str, Any]:
        identifier = str(session_id or "").strip().lower()
        supplied = str(secret or "")
        if len(identifier) != 32 or not all(ch in "0123456789abcdef" for ch in identifier) or not supplied or len(supplied) > 256:
            raise PairingError("Invalid or expired pairing session")
        item = self._sessions.get(identifier)
        if item is None or item["expires_at"] <= self._clock():
            self._sessions.pop(identifier, None)
            raise PairingError("Pairing session expired; scan a new QR code")
        if not hmac.compare_digest(self._secret_hash(supplied), item["secret_hash"]):
            item["attempts"] += 1
            if item["attempts"] >= _MAX_ATTEMPTS:
                self._sessions.pop(identifier, None)
            raise PairingError("Invalid or expired pairing session")
        return item

    def submit_request(self, session_id: str, secret: str, device_name: str) -> dict[str, str]:
        if not isinstance(device_name, str):
            raise PairingError("Device name must be text")
        name = device_name.strip()
        if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            raise PairingError("Device name must contain 1–80 printable characters")
        with self._lock:
            self._purge_expired()
            item = self._get_session(session_id, secret)
            if item["state"] != "waiting" or item["request_id"] is not None:
                raise PairingError("This QR pairing request has already been used")
            request_id = uuid.uuid4().hex
            item["request_id"] = request_id
            item["device_name"] = name
            item["state"] = "pending_approval"
            return {"request_id": request_id, "status": "pending_approval"}

    def pending_requests(self) -> list[dict[str, str]]:
        with self._lock:
            self._purge_expired()
            return [
                {
                    "request_id": item["request_id"],
                    "device_name": item["device_name"],
                    "created_state": item["state"],
                    "expires_at": item["expires_at_utc"],
                }
                for item in self._sessions.values()
                if item["state"] == "pending_approval" and item["request_id"]
            ]

    def approve(self, request_id: str, role: str = ROLE_OPERATOR) -> bool:
        identifier = str(request_id or "").strip().lower()
        if not isinstance(role, str) or role not in _ALLOWED_PAIRING_ROLES:
            raise PairingError("QR pairing cannot grant the admin role; enroll the owner admin locally")
        with self._lock:
            self._purge_expired()
            item = next((value for value in self._sessions.values() if value["request_id"] == identifier), None)
            if item is None or item["state"] != "pending_approval":
                return False
            # Registration persists only a digest in the encrypted device registry.
            credentials = self._registry.register(item["device_name"], role=role)
            item["credentials"] = credentials
            item["state"] = "approved"
            return True

    def deny(self, request_id: str) -> bool:
        identifier = str(request_id or "").strip().lower()
        with self._lock:
            self._purge_expired()
            item = next((value for value in self._sessions.values() if value["request_id"] == identifier), None)
            if item is None or item["state"] != "pending_approval":
                return False
            item["state"] = "denied"
            return True

    def poll_result(self, session_id: str, secret: str) -> dict[str, Any]:
        with self._lock:
            self._purge_expired()
            item = self._get_session(session_id, secret)
            state = item["state"]
            if state == "approved":
                if item["result_consumed"]:
                    raise PairingError("Pairing credentials have already been delivered")
                result = {"status": "approved", "credentials": dict(item["credentials"])}
                item["result_consumed"] = True
                self._sessions.pop(str(session_id).strip().lower(), None)
                return result
            if state == "denied":
                self._sessions.pop(str(session_id).strip().lower(), None)
                return {"status": "denied"}
            if state == "pending_approval":
                return {"status": "pending_approval"}
            return {"status": "waiting"}

    def cancel(self, session_id: str) -> bool:
        identifier = str(session_id or "").strip().lower()
        with self._lock:
            self._purge_expired()
            return self._sessions.pop(identifier, None) is not None


