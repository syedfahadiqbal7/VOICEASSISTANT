from __future__ import annotations

import json

import pytest

from mobile_api.device_registry import MobileDeviceRegistry


def test_device_secret_is_only_returned_once_and_registry_is_encrypted(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "41" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    enrolled = registry.register("Fahad phone")

    raw = (tmp_path / "mobile_devices.json").read_bytes()
    assert raw.startswith(b"VAENC1")
    assert enrolled["device_id"]
    assert enrolled["device_token"]
    assert registry.authenticate(enrolled["device_id"], enrolled["device_token"]) is True
    assert enrolled["device_token"].encode() not in raw


def test_device_authentication_rejects_wrong_secret_and_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "42" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    enrolled = registry.register("Test phone")

    assert registry.authenticate(enrolled["device_id"], "wrong-secret") is False
    assert registry.authenticate("0" * 32, enrolled["device_token"]) is False
    assert registry.authenticate("../" + enrolled["device_id"], enrolled["device_token"]) is False


def test_revocation_is_immediate_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "43" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    enrolled = registry.register("Tablet")

    assert registry.authenticate(enrolled["device_id"], enrolled["device_token"]) is True
    assert registry.revoke(enrolled["device_id"]) is True
    assert registry.authenticate(enrolled["device_id"], enrolled["device_token"]) is False
    assert registry.revoke(enrolled["device_id"]) is False
    assert registry.list_devices()[0]["status"] == "revoked"


def test_invalid_device_name_and_id_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "44" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    with pytest.raises(ValueError):
        registry.register(" ")
    with pytest.raises(ValueError):
        registry.revoke("not-an-id")


def test_registry_presence_disables_shared_token_fallback_even_when_all_revoked(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "45" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    assert registry.is_configured() is False
    enrolled = registry.register("Revoked phone")
    assert registry.is_configured() is True
    registry.revoke(enrolled["device_id"])
    assert registry.is_configured() is True
