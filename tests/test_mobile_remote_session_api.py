from __future__ import annotations

import base64
import hashlib
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import mobile_api.server as server
from agent.remote_identity import RemoteIdentityStore
from mobile_api.device_registry import MobileDeviceRegistry
from mobile_api.remote_sessions import RemoteSessionStore
from mobile_api.signaling import canonical_json


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _enroll(registry, name="Remote API phone"):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    credentials = registry.register(name, public_key_ed25519=_b64(public))
    return private, credentials


def _proof(private, qr):
    grant = {
        "protocol_version": 1,
        "session_id": qr["session_id"],
        "device_id": qr["device_id"],
        "challenge": qr["challenge"],
        "issued_at_ms": qr["issued_at_ms"],
    }
    return _b64(private.sign(canonical_json(grant)))


def _configure(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "91" * 32)
    registry = MobileDeviceRegistry(tmp_path / "devices.json")
    private, credentials = _enroll(registry)
    identity_store = RemoteIdentityStore(tmp_path / "desktop_remote_identity.json")
    identity_store.bootstrap(confirmed=True)
    monkeypatch.setattr(server, "mobile_devices", registry)
    monkeypatch.setattr(server, "remote_identity_store", identity_store)
    monkeypatch.setattr(server, "remote_session_store", RemoteSessionStore())
    return registry, private, credentials, identity_store


def test_remote_qr_is_bound_to_device_endpoint_and_desktop_signature(tmp_path, monkeypatch):
    registry, private, credentials, identity_store = _configure(tmp_path, monkeypatch)

    result = server._create_remote_session_qr({
        "device_id": credentials["device_id"],
        "endpoint_url": "https://voice.example.test",
    })
    desktop = identity_store.status()
    qr = result
    grant = {
        "protocol_version": 1,
        "endpoint": qr["endpoint"],
        "session_id": qr["session_id"],
        "device_id": qr["device_id"],
        "challenge": qr["challenge"],
        "issued_at_ms": qr["issued_at_ms"],
        "expires_at_ms": qr["expires_at_ms"],
        "desktop_identity_id": desktop["identity_id"],
    }
    signature = base64.urlsafe_b64decode(
        qr["grant_signature"] + "=" * (-len(qr["grant_signature"]) % 4)
    )

    assert qr["type"] == "voiceassistant_remote_session"
    assert qr["version"] == 1
    assert qr["device_id"] == credentials["device_id"]
    assert RemoteIdentityStore.verify(
        desktop["public_key"], canonical_json(grant), signature
    )
    assert not RemoteIdentityStore.verify(
        desktop["public_key"], canonical_json({**grant, "endpoint": "https://attacker.test"}),
        signature,
    )
    assert "private_key" not in qr["desktop_identity"]


def test_remote_qr_requires_initialized_desktop_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "92" * 32)
    registry = MobileDeviceRegistry(tmp_path / "devices.json")
    _private, credentials = _enroll(registry)
    monkeypatch.setattr(server, "mobile_devices", registry)
    monkeypatch.setattr(
        server, "remote_identity_store",
        RemoteIdentityStore(tmp_path / "uninitialized_identity.json"),
    )
    monkeypatch.setattr(server, "remote_session_store", RemoteSessionStore())

    from mobile_api.remote_sessions import RemoteSessionError
    with pytest.raises(RemoteSessionError, match="not initialized"):
        server._create_remote_session_qr({
            "device_id": credentials["device_id"],
            "endpoint_url": "https://voice.example.test",
        })


def test_remote_qr_rejects_non_https_and_unapproved_device(tmp_path, monkeypatch):
    registry, _private, credentials, _identity = _configure(tmp_path, monkeypatch)
    from mobile_api.remote_sessions import RemoteSessionError

    with pytest.raises(Exception):
        server._create_remote_session_qr({
            "device_id": credentials["device_id"],
            "endpoint_url": "http://voice.example.test",
        })
    with pytest.raises(RemoteSessionError, match="not actively paired"):
        server._create_remote_session_qr({
            "device_id": "f" * 32,
            "endpoint_url": "https://voice.example.test",
        })


def test_remote_ticket_redeem_authenticate_role_change_and_revoke(tmp_path, monkeypatch):
    registry, private, credentials, _identity = _configure(tmp_path, monkeypatch)
    qr = server._create_remote_session_qr({
        "device_id": credentials["device_id"],
        "endpoint_url": "https://voice.example.test",
    })
    ticket = server._redeem_remote_session_challenge({
        "session_id": qr["session_id"],
        "device_id": qr["device_id"],
        "challenge": qr["challenge"],
        "issued_at_ms": qr["issued_at_ms"],
        "signature": _proof(private, qr),
    })
    headers = {
        "x-voiceassistant-session-id": ticket["session_id"],
        "x-voiceassistant-session-ticket": ticket["access_ticket"],
        "x-voiceassistant-device-id": credentials["device_id"],
    }

    assert server._mobile_principal(headers)["role"] == "operator"
    assert registry.set_role(credentials["device_id"], "viewer")
    assert server._mobile_principal(headers)["role"] == "viewer"
    assert registry.revoke(credentials["device_id"])
    assert server._mobile_principal(headers) is None


def test_invalid_remote_ticket_never_falls_back_to_shared_token(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_TOKEN", "legacy-shared-token-that-is-long-enough")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_HOST", "0.0.0.0")
    monkeypatch.setattr(
        server, "mobile_devices",
        MobileDeviceRegistry(tmp_path / "no_registry.json"),
    )
    monkeypatch.setattr(server, "remote_session_store", RemoteSessionStore())

    principal = server._mobile_principal({
        "x-voiceassistant-session-id": "a" * 32,
        "x-voiceassistant-session-ticket": "invalid-session-ticket-that-is-long",
        "x-voiceassistant-device-id": "b" * 32,
        "x-voiceassistant-token": "legacy-shared-token-that-is-long-enough",
    })
    assert principal is None


def test_remote_session_headers_must_be_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server, "mobile_devices",
        MobileDeviceRegistry(tmp_path / "no_registry.json"),
    )
    monkeypatch.setattr(server, "remote_session_store", RemoteSessionStore())
    assert server._mobile_principal({
        "x-voiceassistant-session-id": "a" * 32,
    }) is None
