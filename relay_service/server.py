"""Stateless-authentication, in-memory rendezvous signaling relay.

The relay forwards signed WebRTC signaling frames only. It does not authorize
desktop commands, persist session data, or terminate WebRTC DTLS/DataChannels.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_ID = re.compile(r"^[0-9a-f]{32}$")
_B64 = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_FRAME_BYTES = 64 * 1024
MAX_GRANT_TTL_MS = 15 * 60 * 1000
CLOCK_SKEW_MS = 30_000
AUTH_TIMEOUT_SECONDS = 5
PEER_WAIT_SECONDS = 120
SEND_TIMEOUT_SECONDS = 5
MAX_SESSIONS = 256
MAX_CONNECTIONS = 512
MAX_CONNECTIONS_PER_IP = 32
MAX_AUTH_ATTEMPTS_PER_MINUTE = 60
MAX_FRAMES_PER_WINDOW = 120
RATE_WINDOW_SECONDS = 10


class RelayAuthError(ValueError):
    """A relay grant or endpoint proof is invalid."""


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _decode_key(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) != 43 or not _B64.fullmatch(value):
        raise RelayAuthError("Invalid public key encoding")
    try:
        raw = base64.urlsafe_b64decode(value + "=")
    except (ValueError, TypeError) as exc:
        raise RelayAuthError("Invalid public key encoding") from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise RelayAuthError("Invalid public key length")
    return raw


def _decode_signature(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) != 86 or not _B64.fullmatch(value):
        raise RelayAuthError("Invalid signature encoding")
    try:
        raw = base64.urlsafe_b64decode(value + "==")
    except (ValueError, TypeError) as exc:
        raise RelayAuthError("Invalid signature encoding") from exc
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise RelayAuthError("Invalid signature length")
    return raw


def _verify(public_key: bytes, signature: Any, message: bytes) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _decode_signature(signature), message
        )
    except (InvalidSignature, ValueError) as exc:
        raise RelayAuthError("Signature verification failed") from exc


def validate_grant(grant: Any, signature: Any, *, session_id: str,
                   audience: str, now_ms: int | None = None) -> dict[str, Any]:
    if not isinstance(grant, dict):
        raise RelayAuthError("Invalid relay grant")
    required = {
        "protocol_version", "audience", "session_id", "desktop_id", "mobile_id",
        "desktop_public_key", "mobile_public_key", "issued_at_ms", "expires_at_ms",
    }
    if set(grant) != required:
        raise RelayAuthError("Relay grant fields do not match protocol")
    if type(grant["protocol_version"]) is not int or grant["protocol_version"] != 1:
        raise RelayAuthError("Unsupported relay protocol")
    if grant["audience"] != audience or grant["session_id"] != session_id:
        raise RelayAuthError("Relay grant audience or session mismatch")
    for name in ("session_id", "desktop_id", "mobile_id"):
        if not isinstance(grant[name], str) or not _ID.fullmatch(grant[name]):
            raise RelayAuthError("Invalid relay identity")
    issued = grant["issued_at_ms"]
    expires = grant["expires_at_ms"]
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(issued) is not int or type(expires) is not int:
        raise RelayAuthError("Invalid relay grant timestamps")
    if issued > now + CLOCK_SKEW_MS or expires <= now:
        raise RelayAuthError("Relay grant is expired or not yet valid")
    if expires <= issued or expires - issued > MAX_GRANT_TTL_MS:
        raise RelayAuthError("Relay grant lifetime is invalid")
    desktop_key = _decode_key(grant["desktop_public_key"])
    _decode_key(grant["mobile_public_key"])
    _verify(desktop_key, signature, canonical_json(grant))
    return grant


def verify_join_proof(grant: dict[str, Any], side: str, challenge: str,
                      issued_at_ms: Any, signature: Any,
                      now_ms: int | None = None) -> None:
    if side not in {"desktop", "mobile"}:
        raise RelayAuthError("Invalid relay role")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(issued_at_ms) is not int or abs(now - issued_at_ms) > CLOCK_SKEW_MS:
        raise RelayAuthError("Join proof timestamp is invalid")
    proof = {
        "protocol_version": 1,
        "session_id": grant["session_id"],
        "side": side,
        "challenge": challenge,
        "grant_sha256": hashlib.sha256(canonical_json(grant)).hexdigest(),
        "issued_at_ms": issued_at_ms,
    }
    public_key = _decode_key(
        grant["desktop_public_key"] if side == "desktop"
        else grant["mobile_public_key"]
    )
    _verify(public_key, signature, canonical_json(proof))


@dataclass
class PeerSession:
    expires_at_ms: int
    grant_sha256: str
    peers: dict[str, WebSocket] = field(default_factory=dict)


class RendezvousHub:
    """Bounded, process-local peer registry; all state disappears on restart."""

    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self.max_sessions = max_sessions
        self._sessions: dict[str, PeerSession] = {}
        self._lock = asyncio.Lock()

    async def attach(self, session_id: str, side: str, socket: WebSocket,
                     expires_at_ms: int, grant_sha256: str) -> bool:
        now = int(time.time() * 1000)
        async with self._lock:
            expired = [key for key, value in self._sessions.items()
                       if value.expires_at_ms <= now or not value.peers]
            for key in expired:
                self._sessions.pop(key, None)
            session = self._sessions.get(session_id)
            if session is None:
                if len(self._sessions) >= self.max_sessions:
                    return False
                session = PeerSession(expires_at_ms=expires_at_ms, grant_sha256=grant_sha256)
                self._sessions[session_id] = session
            if (session.expires_at_ms != expires_at_ms
                    or not hmac.compare_digest(session.grant_sha256, grant_sha256)
                    or side in session.peers):
                return False
            session.peers[side] = socket
            return True

    async def peer(self, session_id: str, side: str) -> WebSocket | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return session.peers.get("mobile" if side == "desktop" else "desktop")

    async def detach(self, session_id: str, side: str, socket: WebSocket) -> WebSocket | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.peers.get(side) is not socket:
                return None
            session.peers.pop(side, None)
            remaining = next(iter(session.peers.values()), None)
            if not session.peers:
                self._sessions.pop(session_id, None)
            else:
                session.changed.set()
            return remaining


class FrameRate:
    def __init__(self):
        self.started = time.monotonic()
        self.count = 0

    def consume(self) -> bool:
        now = time.monotonic()
        if now - self.started >= RATE_WINDOW_SECONDS:
            self.started, self.count = now, 0
        self.count += 1
        return self.count <= MAX_FRAMES_PER_WINDOW


class RelayAdmission:
    """Bound unauthenticated sockets and handshake attempts before crypto work."""

    def __init__(self):
        self._active = 0
        self._active_by_ip: dict[str, int] = {}
        self._attempts_by_ip: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def open(self, host: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            for address, attempts in list(self._attempts_by_ip.items()):
                while attempts and now - attempts[0] >= 60:
                    attempts.popleft()
                if not attempts:
                    self._attempts_by_ip.pop(address, None)
            attempts = self._attempts_by_ip.get(host)
            if attempts is None:
                if len(self._attempts_by_ip) >= 4096:
                    return False
                attempts = deque()
                self._attempts_by_ip[host] = attempts
            if len(attempts) >= MAX_AUTH_ATTEMPTS_PER_MINUTE:
                return False
            attempts.append(now)
            if (
                self._active >= MAX_CONNECTIONS
                or self._active_by_ip.get(host, 0) >= MAX_CONNECTIONS_PER_IP
            ):
                return False
            self._active += 1
            self._active_by_ip[host] = self._active_by_ip.get(host, 0) + 1
            return True

    async def close(self, host: str) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
            remaining = self._active_by_ip.get(host, 0) - 1
            if remaining <= 0:
                self._active_by_ip.pop(host, None)
            else:
                self._active_by_ip[host] = remaining


def create_app(*, audience: str | None = None, hub: RendezvousHub | None = None) -> FastAPI:
    expected_audience = audience or os.environ.get(
        "VOICEASSISTANT_RELAY_AUDIENCE", "voiceassistant-relay"
    )
    relay_hub = hub or RendezvousHub()
    admission = RelayAdmission()
    app = FastAPI(title="VoiceAssistant Rendezvous Relay", version="1.0")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "service": "rendezvous-relay"})

    @app.websocket("/v1/relay/{session_id}/{side}")
    async def relay_socket(websocket: WebSocket, session_id: str, side: str):
        if not _ID.fullmatch(session_id) or side not in {"desktop", "mobile"}:
            await websocket.close(code=1008)
            return
        client_host = websocket.client.host if websocket.client else "unknown"
        if not await admission.open(client_host):
            await websocket.close(code=1013, reason="Relay admission limit exceeded")
            return
        try:
            await websocket.accept()
            challenge = uuid.uuid4().hex + uuid.uuid4().hex
            await websocket.send_text(json.dumps({
                "type": "relay.challenge", "challenge": challenge,
                "protocol_version": 1,
            }, separators=(",", ":")))
            raw_auth = await asyncio.wait_for(
                websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS
            )
            if len(raw_auth.encode("utf-8")) > 16 * 1024:
                raise RelayAuthError("Authentication frame too large")
            auth = json.loads(raw_auth)
            if not isinstance(auth, dict) or set(auth) != {
                "type", "grant", "grant_signature", "issued_at_ms", "proof_signature"
            } or auth["type"] != "relay.authenticate":
                raise RelayAuthError("Invalid relay authentication frame")
            grant = validate_grant(
                auth["grant"], auth["grant_signature"], session_id=session_id,
                audience=expected_audience,
            )
            verify_join_proof(
                grant, side, challenge, auth["issued_at_ms"], auth["proof_signature"]
            )
            if not await relay_hub.attach(
                session_id, side, websocket, grant["expires_at_ms"],
                hashlib.sha256(canonical_json(grant)).hexdigest(),
            ):
                await websocket.close(code=1008, reason="Session is full or expired")
                return

            rate = FrameRate()
            peer_deadline = time.monotonic() + PEER_WAIT_SECONDS
            peer = await relay_hub.peer(session_id, side)
            while peer is None and time.monotonic() < peer_deadline:
                await asyncio.sleep(0.05)
                peer = await relay_hub.peer(session_id, side)
            if peer is None:
                await websocket.close(code=1013, reason="Peer did not connect")
                return

            await websocket.send_text('{"type":"relay.peer_ready","protocol_version":1}')
            while True:
                remaining_seconds = max(
                    0.1, (grant["expires_at_ms"] - int(time.time() * 1000)) / 1000
                )
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=remaining_seconds
                    )
                except asyncio.TimeoutError:
                    await websocket.close(code=1008, reason="Relay grant expired")
                    break
                if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                    await websocket.close(code=1009, reason="Frame too large")
                    break
                if not rate.consume():
                    await websocket.close(code=1013, reason="Rate limit exceeded")
                    break
                peer = await relay_hub.peer(session_id, side)
                if peer is None:
                    await websocket.close(code=1013, reason="Peer disconnected")
                    break
                await asyncio.wait_for(peer.send_text(raw), timeout=SEND_TIMEOUT_SECONDS)
        except WebSocketDisconnect:
            pass
        except (asyncio.TimeoutError, json.JSONDecodeError, UnicodeError, RelayAuthError, ValueError, TypeError):
            await websocket.close(code=1008, reason="Relay authentication or protocol failure")
        except Exception:
            await websocket.close(code=1011, reason="Relay unavailable")
        finally:
            remaining_peer = await relay_hub.detach(session_id, side, websocket)
            if remaining_peer is not None:
                try:
                    await remaining_peer.close(code=1013, reason="Peer disconnected")
                except Exception:
                    pass
            await admission.close(client_host)

    return app


app = create_app()
