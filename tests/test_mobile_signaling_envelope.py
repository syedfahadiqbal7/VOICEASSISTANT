from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mobile_api.device_registry import MobileDeviceRegistry
from mobile_api.signaling import (
    ReplayGuard,
    SignalingEnvelopeError,
    canonical_json,
    create_signed_envelope,
    verify_mobile_signaling_envelope,
    verify_signed_envelope,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _keypair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, _b64(public)


def _envelope(private, sender, recipient, session, *, sequence=1, now=1_700_000_000_000):
    return create_signed_envelope(
        sender_id=sender,
        recipient_id=recipient,
        session_id=session,
        sequence=sequence,
        message_type="sdp.offer",
        payload={"sdp": "v=0", "candidate_index": 0},
        sign=private.sign,
        now_ms=now,
    )


def test_canonical_json_is_stable_across_key_order():
    assert canonical_json({"z": 1, "a": {"y": True, "b": "x"}}) == (
        b'{"a":{"b":"x","y":true},"z":1}'
    )


def test_signed_envelope_round_trip_and_replay_rejection():
    private, public = _keypair()
    sender, recipient, session = "a" * 32, "b" * 32, "c" * 32
    envelope = _envelope(private, sender, recipient, session)
    guard = ReplayGuard()

    verified = verify_signed_envelope(
        envelope, public_key=public, expected_sender_id=sender,
        expected_recipient_id=recipient, expected_session_id=session,
        replay_guard=guard, now_ms=1_700_000_000_001,
    )
    assert verified["message_type"] == "sdp.offer"
    assert verified["payload"]["sdp"] == "v=0"

    with pytest.raises(SignalingEnvelopeError, match="Replayed"):
        verify_signed_envelope(
            envelope, public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=guard, now_ms=1_700_000_000_002,
        )


@pytest.mark.parametrize("field,value", [
    ("recipient_id", "d" * 32),
    ("session_id", "e" * 32),
    ("sequence", 0),
    ("protocol_version", 2),
    ("message_type", "SDP OFFER"),
])
def test_wrong_binding_or_invalid_header_is_rejected(field, value):
    private, public = _keypair()
    sender, recipient, session = "a" * 32, "b" * 32, "c" * 32
    envelope = _envelope(private, sender, recipient, session)
    envelope[field] = value

    with pytest.raises(SignalingEnvelopeError):
        verify_signed_envelope(
            envelope, public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=ReplayGuard(), now_ms=1_700_000_000_001,
        )


def test_tampered_payload_and_wrong_signing_key_are_rejected():
    private, public = _keypair()
    _other_private, other_public = _keypair()
    sender, recipient, session = "a" * 32, "b" * 32, "c" * 32
    envelope = _envelope(private, sender, recipient, session)
    envelope["payload"]["sdp"] = "attacker-controlled"

    with pytest.raises(SignalingEnvelopeError, match="signature"):
        verify_signed_envelope(
            envelope, public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=ReplayGuard(), now_ms=1_700_000_000_001,
        )

    clean = _envelope(private, sender, recipient, session)
    with pytest.raises(SignalingEnvelopeError, match="signature"):
        verify_signed_envelope(
            clean, public_key=other_public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=ReplayGuard(), now_ms=1_700_000_000_001,
        )


@pytest.mark.parametrize("issued,expires,now", [
    (1_699_999_000_000, 1_699_999_030_000, 1_700_000_000_000),
    (1_700_000_020_000, 1_700_000_050_000, 1_700_000_000_000),
    (1_700_000_000_000, 1_700_000_100_001, 1_700_000_000_001),
])
def test_expired_future_or_overlong_envelope_is_rejected(issued, expires, now):
    private, public = _keypair()
    sender, recipient, session = "a" * 32, "b" * 32, "c" * 32
    envelope = _envelope(private, sender, recipient, session, now=issued)
    envelope["expires_at_ms"] = expires
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    envelope["signature"] = _b64(private.sign(canonical_json(unsigned)))

    with pytest.raises(SignalingEnvelopeError):
        verify_signed_envelope(
            envelope, public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=ReplayGuard(), now_ms=now,
        )


def test_mobile_key_is_resolved_from_registry_and_revocation_is_immediate(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", base64.urlsafe_b64encode(b"K" * 32).decode())
    private, public = _keypair()
    registry = MobileDeviceRegistry(tmp_path / "devices.json")
    enrolled = registry.register("Test phone", public_key_ed25519=public)
    envelope = _envelope(private, enrolled["device_id"], "b" * 32, "c" * 32)

    result = verify_mobile_signaling_envelope(
        envelope, registry=registry, expected_recipient_id="b" * 32,
        expected_session_id="c" * 32, replay_guard=ReplayGuard(),
        now_ms=1_700_000_000_001,
    )
    assert result["sender_id"] == enrolled["device_id"]

    assert registry.revoke(enrolled["device_id"])
    with pytest.raises(SignalingEnvelopeError, match="revoked"):
        verify_mobile_signaling_envelope(
            _envelope(private, enrolled["device_id"], "b" * 32, "d" * 32, sequence=2),
            registry=registry, expected_recipient_id="b" * 32,
            expected_session_id="d" * 32, replay_guard=ReplayGuard(),
            now_ms=1_700_000_000_001,
        )


def test_sequence_must_increase_monotonically_per_session_sender():
    private, public = _keypair()
    sender, recipient, session = "a" * 32, "b" * 32, "c" * 32
    guard = ReplayGuard()
    for sequence in (2, 3):
        verify_signed_envelope(
            _envelope(private, sender, recipient, session, sequence=sequence),
            public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=guard, now_ms=1_700_000_000_001,
        )
    with pytest.raises(SignalingEnvelopeError, match="out-of-order"):
        verify_signed_envelope(
            _envelope(private, sender, recipient, session, sequence=1),
            public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=guard, now_ms=1_700_000_000_001,
        )


@pytest.mark.parametrize("payload", [
    {"bad": 1.25},
    {"bad": float("nan")},
    {"deep": [[[[[[[[[[[[[0]]]]]]]]]]]]]},
    {"huge": 9_007_199_254_740_992},
])
def test_nonportable_or_unsafe_payload_values_are_rejected(payload):
    private, _public = _keypair()
    with pytest.raises(SignalingEnvelopeError):
        create_signed_envelope(
            sender_id="a" * 32, recipient_id="b" * 32, session_id="c" * 32,
            sequence=1, message_type="ice.candidate", payload=payload,
            sign=private.sign, now_ms=1_700_000_000_000,
        )


def test_replay_guard_fails_closed_at_capacity():
    private, public = _keypair()
    guard = ReplayGuard(max_entries=1)
    sender, recipient = "a" * 32, "b" * 32
    first = _envelope(private, sender, recipient, "c" * 32, sequence=1)
    second = _envelope(private, sender, recipient, "d" * 32, sequence=1)
    for envelope, session in ((first, "c" * 32), (second, "d" * 32)):
        if session == "c" * 32:
            verify_signed_envelope(
                envelope, public_key=public, expected_sender_id=sender,
                expected_recipient_id=recipient, expected_session_id=session,
                replay_guard=guard, now_ms=1_700_000_000_001,
            )
        else:
            with pytest.raises(SignalingEnvelopeError, match="capacity"):
                verify_signed_envelope(
                    envelope, public_key=public, expected_sender_id=sender,
                    expected_recipient_id=recipient, expected_session_id=session,
                    replay_guard=guard, now_ms=1_700_000_000_001,
                )


def test_replay_guard_rejects_reused_message_id_even_with_new_sequence():
    private, public = _keypair()
    sender, recipient, session = "a" * 32, "b" * 32, "c" * 32
    first = _envelope(private, sender, recipient, session, sequence=1)
    guard = ReplayGuard()
    verify_signed_envelope(
        first, public_key=public, expected_sender_id=sender,
        expected_recipient_id=recipient, expected_session_id=session,
        replay_guard=guard, now_ms=1_700_000_000_001,
    )
    second = _envelope(private, sender, recipient, session, sequence=2)
    second["message_id"] = first["message_id"]
    unsigned = {key: value for key, value in second.items() if key != "signature"}
    second["signature"] = _b64(private.sign(canonical_json(unsigned)))
    with pytest.raises(SignalingEnvelopeError, match="Replayed"):
        verify_signed_envelope(
            second, public_key=public, expected_sender_id=sender,
            expected_recipient_id=recipient, expected_session_id=session,
            replay_guard=guard, now_ms=1_700_000_000_002,
        )


def test_replay_guard_releases_expired_session_capacity():
    guard = ReplayGuard(max_entries=1)
    guard.accept("c" * 32, "a" * 32, 1, "d" * 32, 1000, 500)
    guard.accept("e" * 32, "a" * 32, 1, "f" * 32, 2000, 1000)
    assert len(guard._highest) == 1


def test_complete_signed_envelope_size_includes_signature():
    private, _public = _keypair()
    payload = {
        "a": "x" * 16384,
        "b": "x" * 16384,
        "c": "x" * 16384,
        "d": "x" * 16000,
    }
    with pytest.raises(SignalingEnvelopeError, match="size limit"):
        create_signed_envelope(
            sender_id="a" * 32, recipient_id="b" * 32,
            session_id="c" * 32, sequence=1, message_type="sdp.offer",
            payload=payload, sign=private.sign, now_ms=1_700_000_000_000,
        )
