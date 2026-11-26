from __future__ import annotations

import base64
import hashlib
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_TEST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
_TEST_PUBLIC_RAW = _TEST_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
VALID_PUBLIC_KEY = base64.urlsafe_b64encode(_TEST_PUBLIC_RAW).decode("ascii").rstrip("=")

from mobile_api.pairing import PairingCoordinator, PairingError


class FakeRegistry:
    def __init__(self):
        self.created = []

    def register(self, name, role="operator", *, public_key_ed25519=None):
        item = {
            "device_id": f"{len(self.created) + 1:032x}",
            "device_token": f"secret-{len(self.created) + 1}",
            "name": name,
            "role": role,
            "public_key_ed25519": public_key_ed25519,
        }
        self.created.append(item)
        return item


def _session(coordinator):
    payload = coordinator.create_session("https://assistant.example.com")
    return payload["session_id"], payload["secret"]


def _submit(coordinator, session_id, secret, name):
    proof_message = (
        "voiceassistant-mobile-pairing-v2\\n"
        + session_id.lower() + "\\n"
        + name + "\\n"
        + VALID_PUBLIC_KEY + "\\n"
        + secret
    ).encode("utf-8")
    signature = _TEST_PRIVATE_KEY.sign(proof_message)
    proof = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return coordinator.submit_request(
        session_id, secret, name, VALID_PUBLIC_KEY, proof
    )


def test_qr_payload_contains_only_short_lived_challenge_not_device_credentials():
    coordinator = PairingCoordinator(FakeRegistry())
    payload = coordinator.create_session("https://assistant.example.com")

    assert payload["type"] == "voiceassistant_mobile_pairing"
    assert payload["version"] == 2
    assert payload["endpoint"] == "https://assistant.example.com"
    assert payload["session_id"]
    assert payload["secret"]
    assert "device_token" not in payload
    assert "device_id" not in payload


@pytest.mark.parametrize("endpoint", [
    "http://assistant.example.com",
    "ws://assistant.example.com",
    "wss://assistant.example.com",
    "https://user:password@assistant.example.com",
    "https://assistant.example.com/path",
    "https://assistant.example.com/?token=secret",
    "https://assistant.example.com/#fragment",
    "",
])
def test_pairing_requires_clean_trusted_https_endpoint(endpoint):
    coordinator = PairingCoordinator(FakeRegistry())
    with pytest.raises(PairingError):
        coordinator.create_session(endpoint)


@pytest.mark.parametrize("ttl", [True, False, 0, 29, 301, "120", 1.5])
def test_pairing_lifetime_is_strictly_bounded(ttl):
    coordinator = PairingCoordinator(FakeRegistry())
    with pytest.raises(PairingError):
        coordinator.create_session("https://assistant.example.com", ttl_seconds=ttl)


def test_desktop_approval_is_required_before_credentials_are_created():
    registry = FakeRegistry()
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)

    _submit(coordinator, session_id, secret, "Owner phone")
    assert registry.created == []
    assert coordinator.pending_requests()[0]["device_name"] == "Owner phone"
    assert coordinator.poll_result(session_id, secret)["status"] == "pending_approval"

    assert coordinator.approve(coordinator.pending_requests()[0]["request_id"]) is True
    assert registry.created[0]["role"] == "operator"
    result = coordinator.poll_result(session_id, secret)
    assert result["status"] == "approved"
    assert result["credentials"]["device_id"] == registry.created[0]["device_id"]
    assert result["credentials"]["device_token"] == registry.created[0]["device_token"]


def test_pairing_credentials_are_retried_until_idempotent_receipt_acknowledgement():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.approve(request_id) is True

    first = coordinator.poll_result(session_id, secret)
    second = coordinator.poll_result(session_id, secret)
    assert first["status"] == second["status"] == "approved"
    assert first["credentials"] == second["credentials"]
    assert coordinator.complete_result(session_id, secret) is True
    assert coordinator.complete_result(session_id, secret) is True
    assert coordinator.poll_result(session_id, secret) == {"status": "completed"}


def test_pairing_qr_is_single_request_and_cannot_be_replayed():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "First phone")
    with pytest.raises(PairingError, match="already been used"):
        _submit(coordinator, session_id, secret, "Second phone")


def test_invalid_secret_is_rejected_and_five_attempts_invalidate_session():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    for _ in range(5):
        with pytest.raises(PairingError):
            _submit(coordinator, session_id, "wrong-secret", "Phone")
    with pytest.raises(PairingError):
        _submit(coordinator, session_id, secret, "Phone")


def test_pairing_session_expires_and_cannot_be_used():
    now = [100.0]
    coordinator = PairingCoordinator(FakeRegistry(), clock=lambda: now[0])
    session_id, secret = _session(coordinator)
    now[0] += 121
    with pytest.raises(PairingError, match="expired"):
        _submit(coordinator, session_id, secret, "Phone")


def test_pairing_denial_does_not_enroll_device():
    registry = FakeRegistry()
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Untrusted phone")
    request_id = coordinator.pending_requests()[0]["request_id"]

    assert coordinator.deny(request_id) is True
    assert coordinator.poll_result(session_id, secret) == {"status": "denied"}
    assert registry.created == []


@pytest.mark.parametrize("role", ["admin", None, [], {}, "superuser"])
def test_qr_pairing_cannot_grant_admin_or_malformed_roles(role):
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]

    with pytest.raises(PairingError):
        coordinator.approve(request_id, role=role)


def test_pairing_capacity_is_bounded_and_expired_sessions_are_reclaimed():
    now = [100.0]
    coordinator = PairingCoordinator(FakeRegistry(), clock=lambda: now[0], max_sessions=2)
    _session(coordinator)
    _session(coordinator)
    with pytest.raises(PairingError, match="Too many"):
        _session(coordinator)

    now[0] += 121
    _session(coordinator)
    assert len(coordinator._sessions) == 1


def test_desktop_can_cancel_pairing_session():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    assert coordinator.cancel(session_id) is True
    assert coordinator.cancel(session_id) is False
    with pytest.raises(PairingError):
        _submit(coordinator, session_id, secret, "Phone")



def test_pairing_admin_routes_reject_remote_and_browser_origin_requests():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mobile_api.server import _require_loopback

    local_request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
    )
    _require_loopback(local_request)

    remote_request = SimpleNamespace(
        client=SimpleNamespace(host="192.0.2.25"),
        headers={},
    )
    with pytest.raises(HTTPException) as remote_error:
        _require_loopback(remote_request)
    assert remote_error.value.status_code == 403

    browser_request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"origin": "https://attacker.example"},
    )
    with pytest.raises(HTTPException) as browser_error:
        _require_loopback(browser_request)
    assert browser_error.value.status_code == 403


def test_pairing_rejects_non_string_endpoint_and_device_name():
    coordinator = PairingCoordinator(FakeRegistry())
    with pytest.raises(PairingError, match="endpoint must be a URL string"):
        coordinator.create_session(["https://assistant.example.com"])

    session_id, secret = _session(coordinator)
    with pytest.raises(PairingError, match="Device name must be text"):
        coordinator.submit_request(session_id, secret, {"name": "phone"}, VALID_PUBLIC_KEY, "")



def test_approved_but_undelivered_device_is_revoked_when_desktop_cancels():
    registry = FakeRegistry()
    registry.revoked = []

    def revoke(device_id):
        registry.revoked.append(device_id)
        return True

    registry.revoke = revoke
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.approve(request_id) is True

    assert coordinator.cancel(session_id) is True
    assert registry.revoked == [registry.created[0]["device_id"]]


def test_approved_but_undelivered_device_is_revoked_when_session_expires():
    now = [100.0]
    registry = FakeRegistry()
    registry.revoked = []
    registry.revoke = lambda device_id: registry.revoked.append(device_id) or True
    coordinator = PairingCoordinator(registry, clock=lambda: now[0])
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.approve(request_id) is True

    now[0] += 121
    assert coordinator.pending_requests() == []
    assert registry.revoked == [registry.created[0]["device_id"]]



def test_challenge_lockout_revokes_provisionally_approved_device():
    registry = FakeRegistry()
    registry.revoked = []
    registry.revoke = lambda device_id: registry.revoked.append(device_id) or True
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.approve(request_id) is True

    for _ in range(5):
        with pytest.raises(PairingError):
            coordinator.poll_result(session_id, "wrong-secret")
    assert registry.revoked == [registry.created[0]["device_id"]]



def test_pairing_transport_helper_rejects_plain_http():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from mobile_api.server import _require_https_pairing_request

    with pytest.raises(HTTPException) as error:
        _require_https_pairing_request(SimpleNamespace(url=SimpleNamespace(scheme="http")))
    assert error.value.status_code == 400

    _require_https_pairing_request(SimpleNamespace(url=SimpleNamespace(scheme="https")))



def test_completed_pairing_is_not_revoked_when_desktop_session_is_cancelled():
    registry = FakeRegistry()
    registry.revoked = []
    registry.revoke = lambda device_id: registry.revoked.append(device_id) or True
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.approve(request_id) is True
    coordinator.poll_result(session_id, secret)
    coordinator.complete_result(session_id, secret)

    assert coordinator.cancel(session_id) is True
    assert registry.revoked == []



def test_desktop_session_status_tracks_approval_completion_and_expiry():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    assert coordinator.session_state(session_id) == "waiting"
    _submit(coordinator, session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.session_state(session_id) == "pending_approval"
    assert coordinator.approve(request_id) is True
    assert coordinator.session_state(session_id) == "approved"
    coordinator.poll_result(session_id, secret)
    coordinator.complete_result(session_id, secret)
    assert coordinator.session_state(session_id) == "completed"
    coordinator.cancel(session_id)
    assert coordinator.session_state(session_id) == "expired"


def test_qr_pairing_requires_canonical_ed25519_mobile_public_key():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)

    with pytest.raises(PairingError, match="Ed25519 mobile public key"):
        coordinator.submit_request(session_id, secret, "Phone", "not-a-key", "")


def test_pending_approval_exposes_mobile_key_fingerprint_and_registration_binds_key():
    registry = FakeRegistry()
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)

    _submit(coordinator, session_id, secret, "Trusted phone")
    pending = coordinator.pending_requests()[0]
    expected_fingerprint = hashlib.sha256(_TEST_PUBLIC_RAW).hexdigest()
    assert pending["identity_algorithm"] == "Ed25519"
    assert pending["identity_fingerprint"] == expected_fingerprint

    assert coordinator.approve(pending["request_id"]) is True
    assert registry.created[0]["public_key_ed25519"] == VALID_PUBLIC_KEY


def test_qr_pairing_rejects_invalid_mobile_proof_of_possession():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    with pytest.raises(PairingError, match="proof-of-possession verification failed"):
        coordinator.submit_request(
            session_id, secret, "Phone", VALID_PUBLIC_KEY,
            base64.urlsafe_b64encode(bytes(64)).decode("ascii").rstrip("="),
        )
    assert coordinator.pending_requests() == []
    assert coordinator.poll_result(session_id, secret)["status"] == "waiting"
