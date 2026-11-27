"""Issue desktop-signed, device-bound grants for the external rendezvous relay.

This module creates QR material only. It does not establish a WebSocket, WebRTC
peer connection, or authorize any desktop command.
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
from typing import Any
from urllib.parse import urlsplit

from mobile_api.signaling import canonical_json

_ID = re.compile(r"^[0-9a-f]{32}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]{43}$")
_MIN_TTL_SECONDS = 60
_MAX_TTL_SECONDS = 900


class RelayGrantError(ValueError):
    """A relay grant cannot safely be created."""


def _public_key_bytes(value: Any, label: str) -> bytes:
    if not isinstance(value, str) or not _B64URL.fullmatch(value):
        raise RelayGrantError(f"{label} public key is missing or malformed")
    try:
        raw = base64.urlsafe_b64decode(value + "=")
    except (ValueError, TypeError) as error:
        raise RelayGrantError(f"{label} public key is malformed") from error
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise RelayGrantError(f"{label} public key is not canonical Ed25519 base64url")
    return raw


def _validate_relay_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise RelayGrantError("Relay URL must be a valid WSS URL")
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        valid = (
            parsed.scheme.lower() == "wss"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.path in ("", "/")
            and parsed.port in (None, 443, 8443)
        )
    except ValueError:
        valid = False
    if not valid:
        raise RelayGrantError("Relay URL must use wss:// with a host and no credentials, query, fragment or custom path")
    return candidate.rstrip("/")


def create_relay_qr_payload(
    *,
    device_id: str,
    relay_url: str,
    audience: str,
    identity_store,
    device_registry,
    ttl_seconds: int = 300,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Create a signed grant binding the desktop, enrolled mobile key and relay."""
    if not isinstance(device_id, str) or not _ID.fullmatch(device_id):
        raise RelayGrantError("Invalid paired mobile device ID")
    if not isinstance(audience, str) or not audience or len(audience) > 128:
        raise RelayGrantError("Relay audience must contain 1–128 characters")
    if type(ttl_seconds) is not int or not _MIN_TTL_SECONDS <= ttl_seconds <= _MAX_TTL_SECONDS:
        raise RelayGrantError("Relay grant lifetime must be between 60 and 900 seconds")

    endpoint = _validate_relay_url(relay_url)
    desktop = identity_store.status()
    if not isinstance(desktop, dict) or desktop.get("initialized") is not True:
        raise RelayGrantError("Desktop remote identity is not initialized")
    desktop_id = desktop.get("identity_id")
    if not isinstance(desktop_id, str) or not _ID.fullmatch(desktop_id):
        raise RelayGrantError("Desktop remote identity ID is invalid")
    desktop_key = _public_key_bytes(desktop.get("public_key"), "Desktop")

    try:
        mobile = device_registry.get_device_identity(device_id)
    except Exception as error:
        raise RelayGrantError("Paired mobile identity registry is unavailable") from error
    if not isinstance(mobile, dict) or mobile.get("algorithm") != "Ed25519":
        raise RelayGrantError("Mobile device is not actively enrolled with an Ed25519 identity")
    mobile_key = _public_key_bytes(mobile.get("public_key"), "Mobile")

    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(now) is not int or now < 0:
        raise RelayGrantError("Invalid grant clock")
    grant = {
        "protocol_version": 1,
        "audience": audience,
        "session_id": __import__("uuid").uuid4().hex,
        "desktop_id": desktop_id,
        "mobile_id": device_id,
        "desktop_public_key": desktop["public_key"],
        "mobile_public_key": mobile["public_key"],
        "issued_at_ms": now,
        "expires_at_ms": now + ttl_seconds * 1000,
    }
    try:
        signature = identity_store.sign(canonical_json(grant))
    except Exception as error:
        raise RelayGrantError("Desktop identity could not sign the relay grant") from error
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise RelayGrantError("Desktop identity returned an invalid Ed25519 signature")

    fingerprint = hashlib.sha256(desktop_key).hexdigest()
    return {
        "type": "voiceassistant_relay_session",
        "version": 1,
        "relay_url": endpoint,
        "grant": grant,
        "grant_signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        "desktop_identity": {
            "identity_id": desktop_id,
            "algorithm": "Ed25519",
            "public_key": desktop["public_key"],
            "fingerprint_sha256": fingerprint,
        },
        "mobile_identity": {
            "device_id": device_id,
            "algorithm": "Ed25519",
            "fingerprint_sha256": hashlib.sha256(mobile_key).hexdigest(),
        },
    }
