from __future__ import annotations

import base64
import hashlib

import pytest

from agent.storage import atomic_write_json, load_json

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
    from agent.data_protection import decrypt_bytes
    clear = decrypt_bytes(raw, context="mobile_devices.json")
    assert enrolled["device_token"].encode() not in clear
    assert b"token_sha256" in clear


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


def test_server_requires_device_credentials_once_registry_exists(tmp_path, monkeypatch):
    import mobile_api.server as server

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "46" * 32)
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_TOKEN", "legacy-shared-token-that-is-long-enough-123")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_HOST", "0.0.0.0")
    monkeypatch.setattr(server.mobile_devices, "path", tmp_path / "mobile_devices.json")
    enrolled = server.mobile_devices.register("Approved phone")

    assert server._authenticate_mobile_headers({
        "x-voiceassistant-device-id": enrolled["device_id"],
        "x-voiceassistant-device-token": enrolled["device_token"],
    }) is True
    assert server._authenticate_mobile_headers({
        "x-voiceassistant-token": "legacy-shared-token-that-is-long-enough-123",
    }) is False


def test_server_rejects_revoked_device_and_does_not_fall_back(tmp_path, monkeypatch):
    import mobile_api.server as server

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "47" * 32)
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_TOKEN", "legacy-shared-token-that-is-long-enough-123")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_HOST", "0.0.0.0")
    monkeypatch.setattr(server.mobile_devices, "path", tmp_path / "mobile_devices.json")
    enrolled = server.mobile_devices.register("Revoked phone")
    assert server.mobile_devices.revoke(enrolled["device_id"]) is True

    assert server._authenticate_mobile_headers({
        "x-voiceassistant-device-id": enrolled["device_id"],
        "x-voiceassistant-device-token": enrolled["device_token"],
    }) is False
    assert server._authenticate_mobile_headers({
        "x-voiceassistant-token": "legacy-shared-token-that-is-long-enough-123",
    }) is False


def test_legacy_shared_token_mode_remains_backward_compatible(tmp_path, monkeypatch):
    import mobile_api.server as server

    monkeypatch.setattr(server.mobile_devices, "path", tmp_path / "not-created.json")
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_TOKEN", "legacy-shared-token-that-is-long-enough-123")
    assert server._authenticate_mobile_headers({
        "x-voiceassistant-token": "legacy-shared-token-that-is-long-enough-123",
    }) is True
    assert server._authenticate_mobile_headers({
        "x-voiceassistant-token": "wrong",
    }) is False


def test_active_websocket_reauthentication_detects_revocation(tmp_path, monkeypatch):
    import mobile_api.server as server

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "48" * 32)
    monkeypatch.setattr(server.mobile_devices, "path", tmp_path / "mobile_devices.json")
    enrolled = server.mobile_devices.register("Active phone")

    class FakeSocket:
        headers = {
            "x-voiceassistant-device-id": enrolled["device_id"],
            "x-voiceassistant-device-token": enrolled["device_token"],
        }

    socket = FakeSocket()
    assert server._reauthenticate_mobile(socket) is True
    assert server.mobile_devices.revoke(enrolled["device_id"]) is True
    assert server._reauthenticate_mobile(socket) is False


def test_corrupt_device_registry_fails_closed(tmp_path, monkeypatch):
    import mobile_api.server as server

    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "49" * 32)
    path = tmp_path / "mobile_devices.json"
    path.write_bytes(b"VAENC1-corrupt")
    monkeypatch.setattr(server.mobile_devices, "path", path)

    class FakeSocket:
        headers = {
            "x-voiceassistant-device-id": "a" * 32,
            "x-voiceassistant-device-token": "some-secret",
        }

    assert server._reauthenticate_mobile(FakeSocket()) is False


def test_registry_refuses_symlinked_registry_file(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "mobile_devices.json"
    registry = MobileDeviceRegistry(path)
    original_is_symlink = Path.is_symlink

    def is_symlink(candidate):
        return candidate == path or original_is_symlink(candidate)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises(ValueError, match="symbolic links"):
        registry.register("Must be refused")
    assert not path.exists()


def test_registry_refuses_symlinked_lock_sidecar(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "mobile_devices.json"
    lock_path = path.with_name(path.name + ".lock")
    registry = MobileDeviceRegistry(path)
    original_is_symlink = Path.is_symlink

    def is_symlink(candidate):
        return candidate == lock_path or original_is_symlink(candidate)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises(ValueError, match="symbolic links"):
        registry.list_devices()
    assert not path.exists()


def test_registry_starts_at_v2_and_legacy_v1_devices_migrate_without_losing_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "51" * 32)
    path = tmp_path / "mobile_devices.json"
    legacy = {
        "version": 1,
        "devices": [{
            "device_id": "a" * 32,
            "name": "Legacy phone",
            "token_sha256": hashlib.sha256(b"legacy-token").hexdigest(),
            "role": "operator",
            "created_at": "2026-01-01T00:00:00+00:00",
            "revoked_at": None,
            "role_updated_at": "2026-02-01T00:00:00+00:00",
        }],
    }
    atomic_write_json(path, legacy, encrypt=True)

    registry = MobileDeviceRegistry(path)
    listed = registry.list_devices()
    migrated = load_json(path, encrypt=True)

    assert migrated["version"] == 2
    assert len(migrated["devices"]) == 1
    device = migrated["devices"][0]
    for field, value in legacy["devices"][0].items():
        assert device[field] == value
    assert device["key_algorithm"] is None
    assert device["public_key_ed25519"] is None
    assert registry.get_device_identity("a" * 32) is None
    assert listed[0]["identity_fingerprint"] is None


def test_registry_stores_only_validated_ed25519_public_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "52" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    public_raw = bytes(range(32))
    public_key = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")

    enrolled = registry.register("Identity phone", public_key_ed25519=public_key)
    identity = registry.get_device_identity(enrolled["device_id"])
    stored = load_json(registry.path, encrypt=True)["devices"][0]

    assert identity == {
        "device_id": enrolled["device_id"],
        "algorithm": "Ed25519",
        "public_key": public_key,
        "fingerprint_sha256": hashlib.sha256(public_raw).hexdigest(),
    }
    assert stored["key_algorithm"] == "Ed25519"
    assert stored["public_key_ed25519"] == public_key
    assert registry.list_devices()[0]["identity_fingerprint"] == hashlib.sha256(public_raw).hexdigest()
    assert "private_key" not in stored
    assert "private_seed" not in stored


@pytest.mark.parametrize("public_key", ["", "A" * 42, "A" * 43 + "=", "!" * 43])
def test_registry_rejects_invalid_ed25519_public_keys(tmp_path, monkeypatch, public_key):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "53" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    with pytest.raises(ValueError, match="Ed25519 public key"):
        registry.register("Invalid identity", public_key_ed25519=public_key)


def test_registry_v2_missing_identity_fields_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "54" * 32)
    path = tmp_path / "mobile_devices.json"
    atomic_write_json(path, {
        "version": 2,
        "devices": [{
            "device_id": "b" * 32,
            "name": "Malformed v2",
            "token_sha256": hashlib.sha256(b"token").hexdigest(),
            "role": "operator",
            "created_at": "2026-01-01T00:00:00+00:00",
            "revoked_at": None,
        }],
    }, encrypt=True)

    with pytest.raises(ValueError, match="missing required fields"):
        MobileDeviceRegistry(path).list_devices()


def test_revoked_device_identity_is_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "55" * 32)
    registry = MobileDeviceRegistry(tmp_path / "mobile_devices.json")
    public_key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    enrolled = registry.register("Revoked identity", public_key_ed25519=public_key)

    assert registry.get_device_identity(enrolled["device_id"]) is not None
    assert registry.revoke(enrolled["device_id"]) is True
    assert registry.get_device_identity(enrolled["device_id"]) is None


@pytest.mark.parametrize("version", [True, 1.0, "2"])
def test_registry_rejects_non_integer_schema_versions(tmp_path, monkeypatch, version):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", "56" * 32)
    path = tmp_path / "mobile_devices.json"
    atomic_write_json(path, {"version": version, "devices": []}, encrypt=True)

    with pytest.raises(ValueError, match="unsupported or invalid shape"):
        MobileDeviceRegistry(path).list_devices()
