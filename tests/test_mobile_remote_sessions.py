from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobile_api.device_registry import MobileDeviceRegistry
from mobile_api.remote_sessions import RemoteSessionError, RemoteSessionStore
from mobile_api.signaling import canonical_json


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _enroll(registry):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    enrolled = registry.register("Remote test phone", public_key_ed25519=_b64(public))
    return private, enrolled


def _proof(private, challenge, *, issued_at_ms=1_700_000_000_000):
    message = {
        "protocol_version": 1,
        "session_id": challenge["session_id"],
        "device_id": challenge["device_id"],
        "challenge": challenge["challenge"],
        "issued_at_ms": issued_at_ms,
    }
    return _b64(private.sign(canonical_json(message)))


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "7a" * 32)
    registry = MobileDeviceRegistry(tmp_path / "devices.json")
    private, enrolled = _enroll(registry)
    store = RemoteSessionStore()
    challenge = store.create_challenge(
        enrolled["device_id"], registry, now_ms=1_700_000_000_000
    )
    return registry, private, enrolled, store, challenge


def test_remote_session_requires_registered_ed25519_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "7b" * 32)
    registry = MobileDeviceRegistry(tmp_path / "devices.json")
    enrolled = registry.register("Legacy phone")
    with pytest.raises(RemoteSessionError, match="not actively paired"):
        RemoteSessionStore().create_challenge(
            enrolled["device_id"], registry, now_ms=1_700_000_000_000
        )


def test_challenge_proof_issues_ticket_once_and_authenticates(tmp_path, monkeypatch):
    registry, private, enrolled, store, challenge = _setup(tmp_path, monkeypatch)
    ticket = store.complete_challenge(
        session_id=challenge["session_id"],
        device_id=enrolled["device_id"],
        challenge=challenge["challenge"],
        issued_at_ms=1_700_000_000_000,
        signature=_proof(private, challenge),
        registry=registry,
        now_ms=1_700_000_000_001,
    )
    assert ticket["session_id"] == challenge["session_id"]
    assert ticket["access_ticket"]
    assert store.authenticate(
        session_id=ticket["session_id"],
        device_id=enrolled["device_id"],
        access_ticket=ticket["access_ticket"],
        registry=registry,
        now_ms=1_700_000_000_002,
    )
    with pytest.raises(RemoteSessionError, match="already used"):
        store.complete_challenge(
            session_id=challenge["session_id"],
            device_id=enrolled["device_id"],
            challenge=challenge["challenge"],
            issued_at_ms=1_700_000_000_000,
            signature=_proof(private, challenge),
            registry=registry,
            now_ms=1_700_000_000_003,
        )


def test_ticket_is_bound_to_device_and_cannot_be_guessed_or_replayed(tmp_path, monkeypatch):
    registry, _private, enrolled, store, challenge = _setup(tmp_path, monkeypatch)
    ticket = store.complete_challenge(
        session_id=challenge["session_id"], device_id=enrolled["device_id"],
        challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
        signature=_proof(_private, challenge), registry=registry,
        now_ms=1_700_000_000_001,
    )
    assert not store.authenticate(
        session_id=ticket["session_id"], device_id="f" * 32,
        access_ticket=ticket["access_ticket"], registry=registry,
        now_ms=1_700_000_000_002,
    )
    assert not store.authenticate(
        session_id=ticket["session_id"], device_id=enrolled["device_id"],
        access_ticket="wrong-ticket-value-000000000000000",
        registry=registry, now_ms=1_700_000_000_002,
    )


def test_revocation_is_immediate_even_after_ticket_issue(tmp_path, monkeypatch):
    registry, private, enrolled, store, challenge = _setup(tmp_path, monkeypatch)
    ticket = store.complete_challenge(
        session_id=challenge["session_id"], device_id=enrolled["device_id"],
        challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
        signature=_proof(private, challenge), registry=registry,
        now_ms=1_700_000_000_001,
    )
    assert store.authenticate(
        session_id=ticket["session_id"], device_id=enrolled["device_id"],
        access_ticket=ticket["access_ticket"], registry=registry,
        now_ms=1_700_000_000_002,
    )
    assert registry.revoke(enrolled["device_id"])
    assert not store.authenticate(
        session_id=ticket["session_id"], device_id=enrolled["device_id"],
        access_ticket=ticket["access_ticket"], registry=registry,
        now_ms=1_700_000_000_003,
    )


def test_wrong_proof_does_not_consume_challenge_until_attempt_limit(tmp_path, monkeypatch):
    registry, private, enrolled, store, challenge = _setup(tmp_path, monkeypatch)
    for _ in range(4):
        with pytest.raises(RemoteSessionError, match="signature is invalid"):
            store.complete_challenge(
                session_id=challenge["session_id"], device_id=enrolled["device_id"],
                challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
                signature=_b64(b"x" * 64), registry=registry,
                now_ms=1_700_000_000_001,
            )
    with pytest.raises(RemoteSessionError, match="signature is invalid"):
        store.complete_challenge(
            session_id=challenge["session_id"], device_id=enrolled["device_id"],
            challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
            signature=_b64(b"x" * 64), registry=registry,
            now_ms=1_700_000_000_001,
        )
    assert store.snapshot(now_ms=1_700_000_000_002)["pending_challenges"] == 0


def test_expired_challenge_and_ticket_are_rejected(tmp_path, monkeypatch):
    registry, private, enrolled, store, challenge = _setup(tmp_path, monkeypatch)
    with pytest.raises(RemoteSessionError, match="unknown, expired"):
        store.complete_challenge(
            session_id=challenge["session_id"], device_id=enrolled["device_id"],
            challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
            signature=_proof(private, challenge), registry=registry,
            now_ms=1_700_000_091_000,
        )


def test_session_capacity_fails_closed_and_expired_entries_are_pruned(tmp_path, monkeypatch):
    registry, private, enrolled, _store, _challenge = _setup(tmp_path, monkeypatch)
    store = RemoteSessionStore(max_sessions=1)
    challenge = store.create_challenge(enrolled["device_id"], registry, now_ms=1_700_000_000_000)
    ticket = store.complete_challenge(
        session_id=challenge["session_id"], device_id=enrolled["device_id"],
        challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
        signature=_proof(private, challenge), registry=registry,
        now_ms=1_700_000_000_001,
    )
    with pytest.raises(RemoteSessionError, match="capacity"):
        store.create_challenge(enrolled["device_id"], registry, now_ms=1_700_000_000_002)
    assert store.snapshot(now_ms=1_700_601_000_000)["active_sessions"] == 0
    store.create_challenge(enrolled["device_id"], registry, now_ms=1_700_601_000_001)


def test_session_can_be_revoked_explicitly(tmp_path, monkeypatch):
    registry, private, enrolled, store, challenge = _setup(tmp_path, monkeypatch)
    ticket = store.complete_challenge(
        session_id=challenge["session_id"], device_id=enrolled["device_id"],
        challenge=challenge["challenge"], issued_at_ms=1_700_000_000_000,
        signature=_proof(private, challenge), registry=registry,
        now_ms=1_700_000_000_001,
    )
    assert store.revoke_session(ticket["session_id"])
    assert not store.authenticate(
        session_id=ticket["session_id"], device_id=enrolled["device_id"],
        access_ticket=ticket["access_ticket"], registry=registry,
        now_ms=1_700_000_000_002,
    )
