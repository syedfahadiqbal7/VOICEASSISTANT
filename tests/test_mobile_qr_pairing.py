from __future__ import annotations

import pytest

from mobile_api.pairing import PairingCoordinator, PairingError


class FakeRegistry:
    def __init__(self):
        self.created = []

    def register(self, name, role="operator"):
        item = {
            "device_id": f"{len(self.created) + 1:032x}",
            "device_token": f"secret-{len(self.created) + 1}",
            "name": name,
            "role": role,
        }
        self.created.append(item)
        return item


def _session(coordinator):
    payload = coordinator.create_session("https://assistant.example.com")
    return payload["session_id"], payload["secret"]


def test_qr_payload_contains_only_short_lived_challenge_not_device_credentials():
    coordinator = PairingCoordinator(FakeRegistry())
    payload = coordinator.create_session("https://assistant.example.com")

    assert payload["type"] == "voiceassistant_mobile_pairing"
    assert payload["version"] == 1
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

    coordinator.submit_request(session_id, secret, "Owner phone")
    assert registry.created == []
    assert coordinator.pending_requests()[0]["device_name"] == "Owner phone"
    assert coordinator.poll_result(session_id, secret)["status"] == "pending_approval"

    assert coordinator.approve(coordinator.pending_requests()[0]["request_id"]) is True
    assert registry.created[0]["role"] == "operator"
    result = coordinator.poll_result(session_id, secret)
    assert result["status"] == "approved"
    assert result["credentials"]["device_id"] == registry.created[0]["device_id"]
    assert result["credentials"]["device_token"] == registry.created[0]["device_token"]


def test_pairing_credentials_can_be_delivered_only_once():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    coordinator.submit_request(session_id, secret, "Phone")
    request_id = coordinator.pending_requests()[0]["request_id"]
    assert coordinator.approve(request_id) is True

    assert coordinator.poll_result(session_id, secret)["status"] == "approved"
    with pytest.raises(PairingError, match="already delivered"):
        coordinator.poll_result(session_id, secret)


def test_pairing_qr_is_single_request_and_cannot_be_replayed():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    coordinator.submit_request(session_id, secret, "First phone")
    with pytest.raises(PairingError, match="already been used"):
        coordinator.submit_request(session_id, secret, "Second phone")


def test_invalid_secret_is_rejected_and_five_attempts_invalidate_session():
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    for _ in range(5):
        with pytest.raises(PairingError):
            coordinator.submit_request(session_id, "wrong-secret", "Phone")
    with pytest.raises(PairingError):
        coordinator.submit_request(session_id, secret, "Phone")


def test_pairing_session_expires_and_cannot_be_used():
    now = [100.0]
    coordinator = PairingCoordinator(FakeRegistry(), clock=lambda: now[0])
    session_id, secret = _session(coordinator)
    now[0] += 121
    with pytest.raises(PairingError, match="expired"):
        coordinator.submit_request(session_id, secret, "Phone")


def test_pairing_denial_does_not_enroll_device():
    registry = FakeRegistry()
    coordinator = PairingCoordinator(registry)
    session_id, secret = _session(coordinator)
    coordinator.submit_request(session_id, secret, "Untrusted phone")
    request_id = coordinator.pending_requests()[0]["request_id"]

    assert coordinator.deny(request_id) is True
    assert coordinator.poll_result(session_id, secret) == {"status": "denied"}
    assert registry.created == []


@pytest.mark.parametrize("role", ["admin", None, [], {}, "superuser"])
def test_qr_pairing_cannot_grant_admin_or_malformed_roles(role):
    coordinator = PairingCoordinator(FakeRegistry())
    session_id, secret = _session(coordinator)
    coordinator.submit_request(session_id, secret, "Phone")
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
        coordinator.submit_request(session_id, secret, "Phone")
