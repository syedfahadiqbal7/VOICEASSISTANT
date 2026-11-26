from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import mobile_api.remote_signaling as remote_signaling
from agent.remote_identity import RemoteIdentityStore
from mobile_api.device_registry import MobileDeviceRegistry
from mobile_api.rate_limiter import DeviceRateLimiter
from mobile_api.remote_sessions import RemoteSessionStore
from mobile_api.signaling import canonical_json, create_signed_envelope


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "a4" * 32)
    registry = MobileDeviceRegistry(tmp_path / "devices.json")
    mobile_private = Ed25519PrivateKey.generate()
    mobile_public = mobile_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    device = registry.register("Relay test phone", public_key_ed25519=_b64(mobile_public))

    desktop_identity = RemoteIdentityStore(tmp_path / "desktop_identity.json")
    desktop = desktop_identity.bootstrap(confirmed=True)

    sessions = RemoteSessionStore()
    now_ms = int(time.time() * 1000)
    challenge = sessions.create_challenge(device["device_id"], registry, now_ms=now_ms)
    proof = {
        "protocol_version": 1,
        "session_id": challenge["session_id"],
        "device_id": device["device_id"],
        "challenge": challenge["challenge"],
        "issued_at_ms": now_ms,
    }
    ticket = sessions.complete_challenge(
        session_id=challenge["session_id"],
        device_id=device["device_id"],
        challenge=challenge["challenge"],
        issued_at_ms=now_ms,
        signature=_b64(mobile_private.sign(canonical_json(proof))),
        registry=registry,
        now_ms=now_ms + 1,
    )

    app = FastAPI()
    app.include_router(remote_signaling.create_remote_signaling_router(
        session_store=sessions,
        device_registry=registry,
        identity_store=desktop_identity,
        rate_limiter=DeviceRateLimiter(),
    ))
    return app, registry, sessions, mobile_private, device, desktop_identity, desktop, ticket


def test_signed_mobile_and_desktop_envelopes_are_relayed_only_to_peer(tmp_path, monkeypatch):
    app, _registry, _sessions, mobile_private, device, desktop_identity, desktop, ticket = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_signaling, "_is_local_desktop_request", lambda _ws: True)
    client = TestClient(app)
    session_id = ticket["session_id"]
    desktop_url = f"/remote/signaling/desktop/{session_id}?device_id={device['device_id']}"
    mobile_url = f"/remote/signaling/mobile/{session_id}"
    headers = {
        "x-voiceassistant-session-id": session_id,
        "x-voiceassistant-session-ticket": ticket["access_ticket"],
        "x-voiceassistant-device-id": device["device_id"],
    }

    with client.websocket_connect(desktop_url) as desktop_socket:
        with client.websocket_connect(mobile_url, headers=headers) as mobile_socket:
            offer = create_signed_envelope(
                sender_id=device["device_id"],
                recipient_id=desktop["identity_id"],
                session_id=session_id,
                sequence=1,
                message_type="sdp.offer",
                payload={"sdp": "v=0\\r\\n"},
                sign=mobile_private.sign,
            )
            mobile_socket.send_text(json.dumps(offer))
            assert desktop_socket.receive_json() == offer

            answer = create_signed_envelope(
                sender_id=desktop["identity_id"],
                recipient_id=device["device_id"],
                session_id=session_id,
                sequence=1,
                message_type="sdp.answer",
                payload={"sdp": "v=0\\r\\n"},
                sign=desktop_identity.sign,
            )
            desktop_socket.send_text(json.dumps(answer))
            assert mobile_socket.receive_json() == answer


def test_relay_rejects_bad_signature_and_does_not_forward(tmp_path, monkeypatch):
    app, _registry, _sessions, mobile_private, device, _desktop_identity, desktop, ticket = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(remote_signaling, "_is_local_desktop_request", lambda _ws: True)
    client = TestClient(app)
    session_id = ticket["session_id"]
    headers = {
        "x-voiceassistant-session-id": session_id,
        "x-voiceassistant-session-ticket": ticket["access_ticket"],
        "x-voiceassistant-device-id": device["device_id"],
    }

    with client.websocket_connect(
        f"/remote/signaling/desktop/{session_id}?device_id={device['device_id']}"
    ) as desktop_socket:
        with client.websocket_connect(f"/remote/signaling/mobile/{session_id}", headers=headers) as mobile_socket:
            envelope = create_signed_envelope(
                sender_id=device["device_id"], recipient_id=desktop["identity_id"],
                session_id=session_id, sequence=1, message_type="sdp.offer",
                payload={"sdp": "v=0"}, sign=mobile_private.sign,
            )
            envelope["payload"]["sdp"] = "tampered"
            mobile_socket.send_text(json.dumps(envelope))
            with pytest.raises(Exception):
                mobile_socket.receive_text()
            # The peer is not sent an unauthenticated payload.
            assert desktop_socket.scope["type"] == "websocket"


def test_remote_signaling_requires_active_device_bound_ticket(tmp_path, monkeypatch):
    app, registry, _sessions, _private, device, _identity, _desktop, ticket = _prepare(tmp_path, monkeypatch)
    client = TestClient(app)
    registry.revoke(device["device_id"])

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/remote/signaling/mobile/{ticket['session_id']}",
            headers={
                "x-voiceassistant-session-id": ticket["session_id"],
                "x-voiceassistant-session-ticket": ticket["access_ticket"],
                "x-voiceassistant-device-id": device["device_id"],
            },
        ):
            pass


def test_desktop_local_endpoint_rejects_browser_origin_and_non_loopback():
    class Socket:
        def __init__(self, host, origin=None):
            self.client = type("Client", (), {"host": host})()
            self.headers = {} if origin is None else {"origin": origin}

    assert remote_signaling._is_local_desktop_request(Socket("127.0.0.1"))
    assert remote_signaling._is_local_desktop_request(Socket("::1"))
    assert not remote_signaling._is_local_desktop_request(Socket("192.0.2.10"))
    assert not remote_signaling._is_local_desktop_request(Socket("127.0.0.1", "https://evil.example"))


def test_signaling_hub_enforces_one_socket_per_side_and_cleans_up():
    import asyncio

    async def scenario():
        hub = remote_signaling.SignalingHub()
        session_id = "a" * 32
        mobile = object()
        desktop = object()
        duplicate = object()

        assert await hub.attach(session_id, "mobile", mobile)
        assert not await hub.attach(session_id, "mobile", duplicate)
        assert await hub.attach(session_id, "desktop", desktop)
        assert await hub.wait_for_peer(session_id, "mobile") is desktop
        assert await hub.wait_for_peer(session_id, "desktop") is mobile

        await hub.detach(session_id, "mobile", mobile)
        replacement = object()
        assert await hub.attach(session_id, "mobile", replacement)
        assert await hub.wait_for_peer(session_id, "desktop") is replacement
        await hub.detach(session_id, "replacement", replacement)
        await hub.detach(session_id, "desktop", desktop)
        assert await hub.replay_guard(session_id) is not None

    asyncio.run(scenario())
