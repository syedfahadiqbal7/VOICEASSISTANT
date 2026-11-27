"""Outbound desktop connection to the VoiceAssistant rendezvous relay.

This module authenticates the desktop endpoint only. It does not create a
WebRTC peer connection or authorize commands.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from mobile_api.signaling import canonical_json

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_CHALLENGE = re.compile(r"^[0-9a-f]{64}$")
_MAX_FRAME_BYTES = 64 * 1024


class RelayClientError(RuntimeError):
    """The desktop could not safely authenticate or maintain its relay link."""


def _decode_signature(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) != 86:
        raise RelayClientError("Relay grant signature is malformed")
    try:
        raw = base64.urlsafe_b64decode(value + "==")
    except (ValueError, TypeError) as error:
        raise RelayClientError("Relay grant signature is malformed") from error
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise RelayClientError("Relay grant signature is not canonical Ed25519 base64url")
    return raw


def _relay_socket_url(relay_url: Any, session_id: Any) -> str:
    if not isinstance(relay_url, str) or len(relay_url) > 2048:
        raise RelayClientError("Relay URL is invalid")
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise RelayClientError("Relay session ID is invalid")
    try:
        parsed = urlsplit(relay_url)
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
        raise RelayClientError("Relay URL must be a WSS origin on port 443 or 8443")
    return relay_url.rstrip("/") + f"/v1/relay/{session_id}/desktop"


def _validate_payload(payload: Any, identity_store) -> tuple[dict[str, Any], str, str]:
    required = {
        "type", "version", "relay_url", "grant", "grant_signature",
        "desktop_identity", "mobile_identity",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RelayClientError("Relay QR payload fields do not match protocol v1")
    if payload["type"] != "voiceassistant_relay_session" or payload["version"] != 1:
        raise RelayClientError("Unsupported relay QR protocol")
    grant = payload["grant"]
    grant_fields = {
        "protocol_version", "audience", "relay_url", "session_id", "desktop_id", "mobile_id",
        "desktop_public_key", "mobile_public_key", "issued_at_ms", "expires_at_ms",
    }
    if not isinstance(grant, dict) or set(grant) != grant_fields:
        raise RelayClientError("Relay grant fields do not match protocol v1")
    if grant.get("relay_url") != payload.get("relay_url"):
        raise RelayClientError("Relay URL is not covered by the signed grant")
    if type(grant["protocol_version"]) is not int or grant["protocol_version"] != 1:
        raise RelayClientError("Unsupported relay grant version")
    now_ms = int(time.time() * 1000)
    issued, expires = grant["issued_at_ms"], grant["expires_at_ms"]
    if (
        type(issued) is not int or type(expires) is not int
        or issued > now_ms + 30_000 or expires <= now_ms
        or expires <= issued or expires - issued > 900_000
    ):
        raise RelayClientError("Relay grant is expired or has an invalid lifetime")
    desktop = identity_store.status()
    desktop_record = payload["desktop_identity"]
    if (
        not isinstance(desktop, dict) or desktop.get("initialized") is not True
        or desktop.get("algorithm") != "Ed25519"
        or not isinstance(desktop_record, dict)
        or desktop_record.get("identity_id") != desktop.get("identity_id")
        or desktop_record.get("public_key") != desktop.get("public_key")
        or desktop_record.get("fingerprint_sha256") != desktop.get("fingerprint_sha256")
        or grant.get("desktop_id") != desktop.get("identity_id")
        or grant.get("desktop_public_key") != desktop.get("public_key")
    ):
        raise RelayClientError("Relay grant is not bound to this initialized desktop identity")
    mobile = payload["mobile_identity"]
    if (
        not isinstance(mobile, dict)
        or mobile.get("device_id") != grant.get("mobile_id")
        or mobile.get("algorithm") != "Ed25519"
        or not isinstance(mobile.get("fingerprint_sha256"), str)
    ):
        raise RelayClientError("Relay grant is not bound to the selected mobile identity")
    try:
        mobile_public = base64.urlsafe_b64decode(grant["mobile_public_key"] + "=")
    except (ValueError, TypeError) as error:
        raise RelayClientError("Relay grant mobile public key is malformed") from error
    if (
        len(mobile_public) != 32
        or base64.urlsafe_b64encode(mobile_public).decode("ascii").rstrip("=") != grant["mobile_public_key"]
        or hashlib.sha256(mobile_public).hexdigest() != mobile["fingerprint_sha256"]
    ):
        raise RelayClientError("Relay grant mobile key does not match the enrolled device fingerprint")
    signature = _decode_signature(payload["grant_signature"])
    if not identity_store.verify(desktop["public_key"], canonical_json(grant), signature):
        raise RelayClientError("Desktop relay grant signature is invalid")
    endpoint = _relay_socket_url(payload["relay_url"], grant["session_id"])
    return grant, payload["grant_signature"], endpoint


def run_desktop_relay_session(
    payload: dict[str, Any],
    *,
    identity_store,
    stop_event: threading.Event,
    on_status: Callable[[str], None] | None = None,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    peer_wait_timeout: float = 120.0,
) -> None:
    """Connect outbound, prove desktop-key possession, and relay bounded frames.

    The caller should run this function on a dedicated worker thread. Incoming
    frames are surfaced to on_frame but are not interpreted as commands.
    """
    grant, grant_signature, endpoint = _validate_payload(payload, identity_store)
    try:
        from websockets.sync.client import connect
    except ImportError as error:
        raise RelayClientError("The websockets package is required for relay connectivity") from error

    def status(value: str) -> None:
        if on_status is not None:
            on_status(value)

    status("connecting")
    remaining = max(1.0, min(peer_wait_timeout, (grant["expires_at_ms"] - int(time.time() * 1000)) / 1000))
    with connect(
        endpoint,
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        max_size=_MAX_FRAME_BYTES,
    ) as socket:
        raw_challenge = socket.recv(timeout=min(10.0, remaining))
        if not isinstance(raw_challenge, str) or len(raw_challenge.encode("utf-8")) > 4096:
            raise RelayClientError("Relay challenge frame is invalid")
        try:
            challenge_frame = json.loads(raw_challenge)
        except (ValueError, TypeError) as error:
            raise RelayClientError("Relay challenge is not valid JSON") from error
        if (
            not isinstance(challenge_frame, dict)
            or set(challenge_frame) != {"type", "challenge", "protocol_version"}
            or challenge_frame.get("type") != "relay.challenge"
            or challenge_frame.get("protocol_version") != 1
            or not isinstance(challenge_frame.get("challenge"), str)
            or not _CHALLENGE.fullmatch(challenge_frame["challenge"])
        ):
            raise RelayClientError("Relay challenge does not match protocol v1")

        issued_at_ms = int(time.time() * 1000)
        proof = {
            "protocol_version": 1,
            "session_id": grant["session_id"],
            "side": "desktop",
            "challenge": challenge_frame["challenge"],
            "grant_sha256": hashlib.sha256(canonical_json(grant)).hexdigest(),
            "issued_at_ms": issued_at_ms,
        }
        proof_signature = base64.urlsafe_b64encode(
            identity_store.sign(canonical_json(proof))
        ).decode("ascii").rstrip("=")
        auth = {
            "type": "relay.authenticate",
            "grant": grant,
            "grant_signature": grant_signature,
            "issued_at_ms": issued_at_ms,
            "proof_signature": proof_signature,
        }
        socket.send(json.dumps(auth, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        status("authenticated_waiting_for_mobile")

        deadline = time.monotonic() + remaining
        peer_ready = False
        while not stop_event.is_set() and time.monotonic() < deadline:
            try:
                raw_frame = socket.recv(timeout=min(1.0, max(0.1, deadline - time.monotonic())))
            except TimeoutError:
                continue
            if not isinstance(raw_frame, str) or len(raw_frame.encode("utf-8")) > _MAX_FRAME_BYTES:
                raise RelayClientError("Relay returned an invalid or oversized frame")
            try:
                frame = json.loads(raw_frame)
            except (ValueError, TypeError) as error:
                raise RelayClientError("Relay returned a non-JSON frame") from error
            if not isinstance(frame, dict):
                raise RelayClientError("Relay frame must be a JSON object")
            if frame.get("type") == "relay.peer_ready":
                if set(frame) != {"type", "protocol_version"} or frame.get("protocol_version") != 1:
                    raise RelayClientError("Relay peer-ready frame does not match protocol v1")
                if not peer_ready:
                    peer_ready = True
                    status("peer_authenticated")
                continue
            if on_frame is not None:
                on_frame(frame)
        if stop_event.is_set():
            status("stopped")
        elif peer_ready:
            status("grant_expired")
        else:
            status("peer_timeout")
