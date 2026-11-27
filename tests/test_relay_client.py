"""Tests for the outbound desktop rendezvous relay client."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import types

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobile_api.relay_client import (
    RelayClientError,
    _relay_socket_url,
    run_desktop_relay_session,
)
from mobile_api.relay_grants import create_relay_qr_payload
from mobile_api.signaling import canonical_json


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class FakeIdentityStore:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key = _b64(public)
        self.identity_id = "a" * 32
        self.fingerprint = hashlib.sha256(public).hexdigest()

    def status(self):
        return {
            "initialized": True,
            "identity_id": self.identity_id,
            "algorithm": "Ed25519",
            "public_key": self.public_key,
            "fingerprint_sha256": self.fingerprint,
        }

    def sign(self, message: bytes) -> bytes:
        return self.key.sign(message)

    @staticmethod
    def verify(public_key: str, message: bytes, signature: bytes) -> bool:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        raw = base64.urlsafe_b64decode(public_key + "=")
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(signature, message)
            return True
        except Exception:
            return False


class FakeRegistry:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key = _b64(public)
        self.device_id = "b" * 32

    def get_device_identity(self, device_id):
        if device_id != self.device_id:
            return None
        raw = base64.urlsafe_b64decode(self.public_key + "=")
        return {
            "device_id": self.device_id,
            "algorithm": "Ed25519",
            "public_key": self.public_key,
            "fingerprint_sha256": hashlib.sha256(raw).hexdigest(),
        }


class FakeSocket:
    def __init__(self, stop_event):
        self.stop_event = stop_event
        self.sent = []
        self.frames = [
            json.dumps({
                "type": "relay.challenge",
                "challenge": "c" * 64,
                "protocol_version": 1,
            }),
            json.dumps({"type": "relay.peer_ready", "protocol_version": 1}),
        ]

    def recv(self, timeout=None):
        if self.frames:
            return self.frames.pop(0)
        self.stop_event.set()
        raise TimeoutError()

    def send(self, value):
        self.sent.append(json.loads(value))


def _payload():
    identity = FakeIdentityStore()
    registry = FakeRegistry()
    payload = create_relay_qr_payload(
        device_id=registry.device_id,
        relay_url="wss://relay.example.com",
        audience="voiceassistant-relay",
        identity_store=identity,
        device_registry=registry,
        ttl_seconds=300,
    )
    return identity, payload


def test_relay_socket_url_is_strict():
    assert _relay_socket_url("wss://relay.example.com", "a" * 32) == (
        "wss://relay.example.com/v1/relay/" + "a" * 32 + "/desktop"
    )
    for value in (
        "https://relay.example.com",
        "wss://user:pass@relay.example.com",
        "wss://relay.example.com/path",
        "wss://relay.example.com?token=x",
        "wss://relay.example.com:9443",
    ):
        with pytest.raises(RelayClientError):
            _relay_socket_url(value, "a" * 32)


def test_desktop_client_proves_challenge_and_waits_for_authenticated_peer(monkeypatch):
    identity, payload = _payload()
    stop_event = threading.Event()
    fake_socket = FakeSocket(stop_event)
    captured = {}

    class Connection:
        def __enter__(self):
            return fake_socket

        def __exit__(self, *_args):
            return False

    def fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return Connection()

    websockets = types.ModuleType("websockets")
    sync = types.ModuleType("websockets.sync")
    client = types.ModuleType("websockets.sync.client")
    client.connect = fake_connect
    monkeypatch.setitem(sys.modules, "websockets", websockets)
    monkeypatch.setitem(sys.modules, "websockets.sync", sync)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", client)

    statuses = []
    run_desktop_relay_session(
        payload,
        identity_store=identity,
        stop_event=stop_event,
        on_status=statuses.append,
    )

    assert captured["uri"].endswith("/v1/relay/" + payload["grant"]["session_id"] + "/desktop")
    assert captured["kwargs"]["max_size"] == 64 * 1024
    assert statuses == ["connecting", "authenticated_waiting_for_mobile", "peer_authenticated", "stopped"]
    assert len(fake_socket.sent) == 1
    auth = fake_socket.sent[0]
    assert set(auth) == {"type", "grant", "grant_signature", "issued_at_ms", "proof_signature"}
    assert auth["type"] == "relay.authenticate"
    proof = {
        "protocol_version": 1,
        "session_id": payload["grant"]["session_id"],
        "side": "desktop",
        "challenge": "c" * 64,
        "grant_sha256": hashlib.sha256(canonical_json(payload["grant"])).hexdigest(),
        "issued_at_ms": auth["issued_at_ms"],
    }
    signature = base64.urlsafe_b64decode(auth["proof_signature"] + "==")
    assert identity.verify(identity.public_key, canonical_json(proof), signature)


def test_client_rejects_qr_grant_for_another_desktop_identity():
    identity, payload = _payload()
    payload["grant"]["desktop_id"] = "d" * 32
    with pytest.raises(RelayClientError, match="not bound"):
        run_desktop_relay_session(
            payload,
            identity_store=identity,
            stop_event=threading.Event(),
        )


def test_client_rejects_expired_grant():
    identity, payload = _payload()
    payload["grant"]["expires_at_ms"] = payload["grant"]["issued_at_ms"]
    with pytest.raises(RelayClientError, match="expired"):
        run_desktop_relay_session(
            payload,
            identity_store=identity,
            stop_event=threading.Event(),
        )
