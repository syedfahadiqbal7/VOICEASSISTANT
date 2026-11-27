from __future__ import annotations

import asyncio
import base64
import hashlib
import time

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relay_service.server import RelayAdmission, RendezvousHub, create_app, canonical_json, validate_grant


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _keypair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, _b64(public)


def _grant(desktop_key, desktop_public, mobile_public, *, session_id=None, expires=None):
    now = int(time.time() * 1000)
    value = {
        "protocol_version": 1,
        "audience": "test-relay",
        "relay_url": "wss://relay.example.com",
        "session_id": session_id or "1" * 32,
        "desktop_id": "2" * 32,
        "mobile_id": "3" * 32,
        "desktop_public_key": desktop_public,
        "mobile_public_key": mobile_public,
        "issued_at_ms": now,
        "expires_at_ms": expires or now + 60_000,
    }
    return value, _b64(desktop_key.sign(canonical_json(value)))


def _authenticate(socket, side, grant, grant_signature, private_key):
    challenge = socket.receive_json()
    assert challenge["type"] == "relay.challenge"
    proof = {
        "protocol_version": 1,
        "session_id": grant["session_id"],
        "side": side,
        "challenge": challenge["challenge"],
        "grant_sha256": hashlib.sha256(canonical_json(grant)).hexdigest(),
        "issued_at_ms": int(time.time() * 1000),
    }
    socket.send_json({
        "type": "relay.authenticate",
        "grant": grant,
        "grant_signature": grant_signature,
        "issued_at_ms": proof["issued_at_ms"],
        "proof_signature": _b64(private_key.sign(canonical_json(proof))),
    })


def test_relay_forwards_only_after_both_signed_peers_join():
    desktop_key, desktop_public = _keypair()
    mobile_key, mobile_public = _keypair()
    grant, signature = _grant(desktop_key, desktop_public, mobile_public)
    client = TestClient(create_app(audience="test-relay"))

    with client.websocket_connect(f"/v1/relay/{grant['session_id']}/desktop") as desktop:
        _authenticate(desktop, "desktop", grant, signature, desktop_key)
        with client.websocket_connect(f"/v1/relay/{grant['session_id']}/mobile") as mobile:
            _authenticate(mobile, "mobile", grant, signature, mobile_key)
            assert desktop.receive_json()["type"] == "relay.peer_ready"
            assert mobile.receive_json()["type"] == "relay.peer_ready"
            message = '{"type":"sdp.offer","payload":{"sdp":"signed-by-client"}}'
            desktop.send_text(message)
            assert mobile.receive_text() == message
            response = '{"type":"sdp.answer","payload":{"sdp":"signed-by-client"}}'
            mobile.send_text(response)
            assert desktop.receive_text() == response


def test_relay_rejects_invalid_desktop_grant_signature():
    desktop_key, desktop_public = _keypair()
    other_key, _ = _keypair()
    _, mobile_public = _keypair()
    grant, _ = _grant(desktop_key, desktop_public, mobile_public)
    bad_signature = _b64(other_key.sign(canonical_json(grant)))
    try:
        validate_grant(
            grant, bad_signature, session_id=grant["session_id"],
            audience="test-relay",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid grant signature was accepted")


def test_relay_rejects_wrong_audience_and_expired_grants():
    desktop_key, desktop_public = _keypair()
    _, mobile_public = _keypair()
    grant, signature = _grant(desktop_key, desktop_public, mobile_public)
    try:
        validate_grant(
            grant, signature, session_id=grant["session_id"],
            audience="different-relay",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Wrong audience was accepted")

    expired, expired_signature = _grant(
        desktop_key, desktop_public, mobile_public, expires=int(time.time() * 1000) - 1
    )
    try:
        validate_grant(
            expired, expired_signature, session_id=expired["session_id"],
            audience="test-relay",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expired grant was accepted")


def test_relay_health_is_non_sensitive():
    response = TestClient(create_app(audience="test-relay")).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "rendezvous-relay"}



def test_hub_rejects_peers_with_different_signed_grants():
    async def scenario():
        hub = RendezvousHub()
        first = object()
        second = object()
        expires = int(time.time() * 1000) + 60_000
        assert await hub.attach("4" * 32, "desktop", first, expires, "a" * 64)
        assert not await hub.attach("4" * 32, "mobile", second, expires, "b" * 64)

    asyncio.run(scenario())




def test_admission_bounds_global_and_per_ip_connections():
    async def scenario():
        per_ip = RelayAdmission()
        for _ in range(32):
            assert await per_ip.open("192.0.2.10")
        assert not await per_ip.open("192.0.2.10")
        for _ in range(32):
            await per_ip.close("192.0.2.10")

        global_limit = RelayAdmission()
        for index in range(512):
            assert await global_limit.open(f"198.51.100.{index}")
        assert not await global_limit.open("203.0.113.1")

    asyncio.run(scenario())



def test_hub_rejects_expired_session_and_does_not_return_expired_peer():
    async def scenario():
        hub = RendezvousHub()
        peer = object()
        expired_at = int(time.time() * 1000) - 1
        assert not await hub.attach(
            "5" * 32, "desktop", peer, expired_at, "c" * 64
        )
        assert await hub.peer("5" * 32, "desktop") is None

        valid_until = int(time.time() * 1000) + 60_000
        assert await hub.attach(
            "6" * 32, "desktop", peer, valid_until, "d" * 64
        )
        hub._sessions["6" * 32].expires_at_ms = expired_at
        assert await hub.peer("6" * 32, "desktop") is None

    asyncio.run(scenario())
