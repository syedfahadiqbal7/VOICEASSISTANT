"""Authenticated WebSocket relay for signed WebRTC signaling envelopes.

This relay carries only offer/answer/ICE control messages. It does not proxy
media, authorize desktop commands, or replace the existing /ws command channel.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from mobile_api.signaling import (
    MAX_ENVELOPE_BYTES,
    ReplayGuard,
    SignalingEnvelopeError,
    verify_desktop_signaling_envelope,
    verify_mobile_signaling_envelope,
)

logger = logging.getLogger("voiceassistant.remote_signaling")
_ALLOWED_MESSAGE_TYPES = {"sdp.offer", "sdp.answer", "ice.candidate", "session.end"}
_PEER_WAIT_SECONDS = 30
_REVALIDATE_SECONDS = 10


class SignalingHub:
    """One mobile and one local desktop signaling socket per active session."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connections: dict[str, dict[str, WebSocket]] = {}
        self._peer_events: dict[str, asyncio.Event] = {}
        self._replay_guards: dict[str, ReplayGuard] = {}

    async def attach(self, session_id: str, side: str, websocket: WebSocket) -> bool:
        if side not in {"mobile", "desktop"}:
            return False
        async with self._lock:
            peers = self._connections.setdefault(session_id, {})
            if side in peers:
                return False
            peers[side] = websocket
            event = self._peer_events.setdefault(session_id, asyncio.Event())
            if len(peers) == 2:
                event.set()
            self._replay_guards.setdefault(session_id, ReplayGuard())
            return True

    async def wait_for_peer(self, session_id: str, side: str) -> WebSocket | None:
        async with self._lock:
            peers = self._connections.get(session_id, {})
            peer = peers.get("desktop" if side == "mobile" else "mobile")
            if peer is not None:
                return peer
            event = self._peer_events.get(session_id)
        if event is None:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=_PEER_WAIT_SECONDS)
        except asyncio.TimeoutError:
            return None
        async with self._lock:
            peers = self._connections.get(session_id, {})
            return peers.get("desktop" if side == "mobile" else "mobile")

    async def replay_guard(self, session_id: str) -> ReplayGuard:
        async with self._lock:
            return self._replay_guards.setdefault(session_id, ReplayGuard())

    async def detach(self, session_id: str, side: str, websocket: WebSocket) -> None:
        async with self._lock:
            peers = self._connections.get(session_id)
            if peers is None or peers.get(side) is not websocket:
                return
            peers.pop(side, None)
            if not peers:
                self._connections.pop(session_id, None)
                self._peer_events.pop(session_id, None)
                self._replay_guards.pop(session_id, None)
            else:
                event = self._peer_events.get(session_id)
                if event is not None:
                    event.clear()


def _is_local_desktop_request(websocket: WebSocket) -> bool:
    """The desktop signaling socket is intentionally loopback-only."""
    if websocket.headers.get("origin"):
        return False
    client = websocket.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


def create_remote_signaling_router(
    *, session_store, device_registry, identity_store, rate_limiter,
    hub: SignalingHub | None = None,
) -> APIRouter:
    router = APIRouter()
    relay = hub or SignalingHub()

    async def _close(websocket: WebSocket, code: int, reason: str) -> None:
        try:
            await websocket.close(code=code, reason=reason)
        except Exception:
            pass

    async def _relay(websocket: WebSocket, *, session_id: str, device_id: str,
                     side: str, access_ticket: str = "") -> None:
        try:
            await websocket.accept()
            if not await relay.attach(session_id, side, websocket):
                await _close(websocket, 1008, "A signaling connection already exists for this session")
                return
            replay_guard = await relay.replay_guard(session_id)
            identity = identity_store.status()
            if not identity.get("initialized"):
                await _close(websocket, 1011, "Desktop identity is unavailable")
                return
            desktop_id = identity["identity_id"]

            while True:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=_REVALIDATE_SECONDS)
                except asyncio.TimeoutError:
                    if side == "mobile":
                        active = session_store.authenticate(
                            session_id=session_id, device_id=device_id,
                            access_ticket=access_ticket, registry=device_registry,
                        )
                    else:
                        active = session_store.authorize_desktop(
                            session_id=session_id, device_id=device_id,
                            registry=device_registry,
                        )
                    if not active:
                        await _close(websocket, 1008, "Remote session is no longer authorized")
                        break
                    continue

                if len(raw.encode("utf-8")) > MAX_ENVELOPE_BYTES:
                    await _close(websocket, 1009, "Signaling envelope exceeds size limit")
                    break
                try:
                    envelope: Any = json.loads(raw)
                    now_ms = int(time.time() * 1000)
                    if side == "mobile":
                        if not session_store.authenticate(
                            session_id=session_id, device_id=device_id,
                            access_ticket=access_ticket, registry=device_registry,
                            now_ms=now_ms,
                        ):
                            await _close(websocket, 1008, "Remote session is no longer authorized")
                            break
                        # Count every frame before cryptographic verification so invalid
                        # signatures cannot bypass the per-device CPU/connection quota.
                        decision = rate_limiter.consume(device_id, "remote_signaling")
                        if not decision.allowed:
                            await _close(websocket, 1013, "Remote signaling rate limit exceeded")
                            break
                        verified = verify_mobile_signaling_envelope(
                            envelope, registry=device_registry,
                            expected_recipient_id=desktop_id,
                            expected_session_id=session_id,
                            replay_guard=replay_guard, now_ms=now_ms,
                        )
                        if verified["sender_id"] != device_id:
                            raise SignalingEnvelopeError("Envelope sender does not match authenticated device")
                    else:
                        if not session_store.authorize_desktop(
                            session_id=session_id, device_id=device_id,
                            registry=device_registry, now_ms=now_ms,
                        ):
                            await _close(websocket, 1008, "Remote session is no longer authorized")
                            break
                        verified = verify_desktop_signaling_envelope(
                            envelope, identity_store=identity_store,
                            expected_recipient_id=device_id,
                            expected_session_id=session_id,
                            replay_guard=replay_guard, now_ms=now_ms,
                        )
                    if verified["message_type"] not in _ALLOWED_MESSAGE_TYPES:
                        raise SignalingEnvelopeError("Unsupported WebRTC signaling message type")
                except (json.JSONDecodeError, UnicodeError, SignalingEnvelopeError, ValueError, TypeError) as error:
                    logger.warning("Rejected remote signaling envelope: %s", error,
                                   extra={"event": "mobile.remote_signaling_rejected"})
                    await _close(websocket, 1008, "Invalid or unauthorized signaling envelope")
                    break

                peer = await relay.wait_for_peer(session_id, side)
                if peer is None:
                    await _close(websocket, 1013, "The other signaling endpoint is not connected")
                    break
                try:
                    await peer.send_text(raw)
                except Exception:
                    await _close(websocket, 1011, "The other signaling endpoint disconnected")
                    break
                if verified["message_type"] == "session.end":
                    await _close(peer, 1000, "Remote session ended")
                    await _close(websocket, 1000, "Remote session ended")
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Remote signaling relay failed",
                             extra={"event": "mobile.remote_signaling_failed"})
            await _close(websocket, 1011, "Remote signaling failed")
        finally:
            await relay.detach(session_id, side, websocket)

    @router.websocket("/remote/signaling/mobile/{session_id}")
    async def mobile_signaling(websocket: WebSocket, session_id: str):
        device_id = websocket.headers.get("x-voiceassistant-device-id", "")
        access_ticket = websocket.headers.get("x-voiceassistant-session-ticket", "")
        header_session = websocket.headers.get("x-voiceassistant-session-id", "")
        if (
            not device_id or not access_ticket or header_session != session_id
            or not session_store.authenticate(
                session_id=session_id, device_id=device_id,
                access_ticket=access_ticket, registry=device_registry,
            )
        ):
            await _close(websocket, 1008, "Valid device-bound remote session required")
            return
        await _relay(websocket, session_id=session_id, device_id=device_id,
                     side="mobile", access_ticket=access_ticket)

    @router.websocket("/remote/signaling/desktop/{session_id}")
    async def desktop_signaling(websocket: WebSocket, session_id: str, device_id: str):
        if not _is_local_desktop_request(websocket):
            await _close(websocket, 1008, "Desktop signaling is available only to the local desktop process")
            return
        if not session_store.authorize_desktop(
            session_id=session_id, device_id=device_id, registry=device_registry,
        ):
            await _close(websocket, 1008, "Active remote session required")
            return
        await _relay(websocket, session_id=session_id, device_id=device_id, side="desktop")

    return router
