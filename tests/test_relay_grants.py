"""Tests for the external relay QR grant issuer."""
from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobile_api.relay_grants import RelayGrantError, create_relay_qr_payload
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

    def status(self):
        return {
            "initialized": True,
            "identity_id": self.identity_id,
            "algorithm": "Ed25519",
            "public_key": self.public_key,
        }

    def sign(self, message: bytes) -> bytes:
        return self.key.sign(message)


class FakeRegistry:
    def __init__(self, device_id="b" * 32):
        self.device_id = device_id
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_key = _b64(public)

    def get_device_identity(self, device_id):
        if device_id != self.device_id:
            return None
        raw = base64.urlsafe_b64decode(self.public_key + "=")
        return {
            "device_id": device_id,
            "algorithm": "Ed25519",
            "public_key": self.public_key,
            "fingerprint_sha256": hashlib.sha256(raw).hexdigest(),
        }


def test_issues_signed_grant_bound_to_both_enrolled_identities():
    identity = FakeIdentityStore()
    registry = FakeRegistry()
    payload = create_relay_qr_payload(
        device_id=registry.device_id,
        relay_url="wss://relay.example.com",
        audience="voiceassistant-relay",
        identity_store=identity,
        device_registry=registry,
        ttl_seconds=300,
        now_ms=1_800_000_000_000,
    )

    assert payload["type"] == "voiceassistant_relay_session"
    assert payload["relay_url"] == "wss://relay.example.com"
    grant = payload["grant"]
    assert set(grant) == {
        "protocol_version", "audience", "relay_url", "session_id", "desktop_id", "mobile_id",
        "desktop_public_key", "mobile_public_key", "issued_at_ms", "expires_at_ms",
    }
    assert grant["desktop_id"] == identity.identity_id
    assert grant["mobile_id"] == registry.device_id
    assert grant["expires_at_ms"] - grant["issued_at_ms"] == 300_000

    signature = base64.urlsafe_b64decode(payload["grant_signature"] + "==")
    identity.key.public_key().verify(signature, canonical_json(grant))
    assert payload["desktop_identity"]["fingerprint_sha256"] == hashlib.sha256(
        base64.urlsafe_b64decode(identity.public_key + "=")
    ).hexdigest()


@pytest.mark.parametrize("url", [
    "http://relay.example.com",
    "ws://relay.example.com",
    "wss://user:password@relay.example.com",
    "wss://relay.example.com/path",
    "wss://relay.example.com?token=secret",
    "wss://relay.example.com:9443",
])
def test_rejects_unsafe_or_unsupported_relay_urls(url):
    with pytest.raises(RelayGrantError, match="Relay URL"):
        create_relay_qr_payload(
            device_id="b" * 32,
            relay_url=url,
            audience="voiceassistant-relay",
            identity_store=FakeIdentityStore(),
            device_registry=FakeRegistry(),
        )


def test_rejects_uninitialized_desktop_identity():
    class MissingIdentity:
        def status(self):
            return {"initialized": False}

    with pytest.raises(RelayGrantError, match="not initialized"):
        create_relay_qr_payload(
            device_id="b" * 32,
            relay_url="wss://relay.example.com",
            audience="voiceassistant-relay",
            identity_store=MissingIdentity(),
            device_registry=FakeRegistry(),
        )


def test_rejects_device_without_active_ed25519_identity():
    class LegacyRegistry:
        def get_device_identity(self, device_id):
            return None

    with pytest.raises(RelayGrantError, match="not actively enrolled"):
        create_relay_qr_payload(
            device_id="b" * 32,
            relay_url="wss://relay.example.com",
            audience="voiceassistant-relay",
            identity_store=FakeIdentityStore(),
            device_registry=LegacyRegistry(),
        )


def test_rejects_invalid_ttl_and_device_id():
    args = {
        "device_id": "b" * 32,
        "relay_url": "wss://relay.example.com",
        "audience": "voiceassistant-relay",
        "identity_store": FakeIdentityStore(),
        "device_registry": FakeRegistry(),
    }
    with pytest.raises(RelayGrantError, match="lifetime"):
        create_relay_qr_payload(**args, ttl_seconds=901)
    args["device_id"] = "not-a-device"
    with pytest.raises(RelayGrantError, match="device ID"):
        create_relay_qr_payload(**args)
