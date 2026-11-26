"""Encrypted provider connection registry tests."""
from __future__ import annotations

import base64

import pytest

from agent.data_protection import DataProtectionError, is_encrypted_bytes
from agent.integration_registry import IntegrationConnectionStore, IntegrationStoreError


GOOGLE_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _key(monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"I" * 32).decode("ascii"),
    )


def test_google_connection_is_encrypted_and_status_never_exposes_tokens(tmp_path, monkeypatch):
    _key(monkeypatch)
    path = tmp_path / "history" / "integrations.json"
    store = IntegrationConnectionStore(path)
    status = store.save_connection(
        "google",
        "person@example.com",
        GOOGLE_SCOPES,
        "access-token-value-123456",
        "refresh-token-value-123456",
        "2030-01-01T12:00:00+00:00",
    )

    assert is_encrypted_bytes(path.read_bytes())
    assert b"access-token-value" not in path.read_bytes()
    assert b"refresh-token-value" not in path.read_bytes()
    assert status["connected"] is True
    assert status["account_email"] == "person@example.com"
    assert set(status["capabilities"]) == {"email", "calendar"}
    assert "access_token" not in status
    assert "refresh_token" not in status

    connection = store.get_connection("google")
    assert connection["access_token"] == "access-token-value-123456"
    assert connection["refresh_token"] == "refresh-token-value-123456"
    assert store.connected_integrations() == {"email", "calendar"}


def test_microsoft_mail_only_scope_does_not_claim_calendar(tmp_path, monkeypatch):
    _key(monkeypatch)
    store = IntegrationConnectionStore(tmp_path / "integrations.json")
    status = store.save_connection(
        "microsoft",
        "person@example.com",
        ["offline_access", "User.Read", "Mail.Read"],
        "microsoft-access-token-123",
        "microsoft-refresh-token-123",
        "2030-01-01T12:00:00+00:00",
    )
    assert status["capabilities"] == ["email"]
    assert store.connected_integrations() == {"email"}


def test_expired_connection_is_reported_for_refresh_without_exposing_tokens(tmp_path, monkeypatch):
    _key(monkeypatch)
    store = IntegrationConnectionStore(tmp_path / "integrations.json")
    status = store.save_connection(
        "google",
        "person@example.com",
        ["email", "https://www.googleapis.com/auth/gmail.readonly"],
        "expired-access-token-123",
        "refresh-token-value-123456",
        "2020-01-01T00:00:00+00:00",
    )
    assert status["needs_refresh"] is True
    assert "refresh_token" not in status
    assert store.connected_integrations() == set()


def test_disconnect_removes_tokens_and_capabilities(tmp_path, monkeypatch):
    _key(monkeypatch)
    store = IntegrationConnectionStore(tmp_path / "integrations.json")
    store.save_connection(
        "google",
        "person@example.com",
        GOOGLE_SCOPES,
        "access-token-value-123456",
        "refresh-token-value-123456",
        "2030-01-01T12:00:00+00:00",
    )
    assert store.disconnect("google") is True
    assert store.disconnect("google") is False
    assert store.get_status("google")["connected"] is False
    assert store.connected_integrations() == set()


def test_unsupported_provider_and_scope_are_rejected(tmp_path, monkeypatch):
    _key(monkeypatch)
    store = IntegrationConnectionStore(tmp_path / "integrations.json")
    with pytest.raises(ValueError, match="Provider"):
        store.save_connection("unknown", "person@example.com", GOOGLE_SCOPES, "access-token-value-123456", "refresh-token-value-123456", "2030-01-01T12:00:00+00:00")
    with pytest.raises(IntegrationStoreError, match="scopes"):
        store.save_connection("google", "person@example.com", ["https://www.googleapis.com/auth/gmail.modify"], "access-token-value-123456", "refresh-token-value-123456", "2030-01-01T12:00:00+00:00")


def test_connection_requires_refresh_token_and_timezone_aware_expiry(tmp_path, monkeypatch):
    _key(monkeypatch)
    store = IntegrationConnectionStore(tmp_path / "integrations.json")
    with pytest.raises(IntegrationStoreError, match="token"):
        store.save_connection("google", "person@example.com", GOOGLE_SCOPES, "short", "refresh-token-value-123456", "2030-01-01T12:00:00+00:00")
    with pytest.raises(IntegrationStoreError, match="timestamps"):
        store.save_connection("google", "person@example.com", GOOGLE_SCOPES, "access-token-value-123456", "refresh-token-value-123456", "2030-01-01T12:00:00")


def test_corrupt_encrypted_registry_fails_closed(tmp_path, monkeypatch):
    _key(monkeypatch)
    path = tmp_path / "integrations.json"
    store = IntegrationConnectionStore(path)
    store.save_connection("google", "person@example.com", GOOGLE_SCOPES, "access-token-value-123456", "refresh-token-value-123456", "2030-01-01T12:00:00+00:00")
    path.write_bytes(b"VAENC2-corrupt")
    with pytest.raises((DataProtectionError, IntegrationStoreError)):
        store.list_status()


def test_symlinked_integration_store_is_rejected(tmp_path, monkeypatch):
    _key(monkeypatch)
    target = tmp_path / "target.json"
    target.write_text('{"version":1,"connections":{}}', encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not available in this test environment")
    with pytest.raises(DataProtectionError):
        IntegrationConnectionStore(link)


def test_parent_symlink_and_oversized_registry_fail_closed(tmp_path, monkeypatch):
    _key(monkeypatch)
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are not available in this test environment")
    with pytest.raises(DataProtectionError):
        IntegrationConnectionStore(linked_parent / "integrations.json")

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(IntegrationStoreError, match="size limit"):
        IntegrationConnectionStore(oversized)
