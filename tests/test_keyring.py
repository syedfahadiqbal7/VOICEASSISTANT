"""Tests for protected keyring bootstrap and validation (Phase 8.3.1)."""
from __future__ import annotations

import base64
import copy

import pytest

from agent.keyring import (
    KeyringError,
    create_initial_keyring,
    get_active_key,
    get_key,
    load_keyring,
    save_keyring,
    validate_keyring,
)


def _set_wrap_key(monkeypatch, key=b"W" * 32):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(key).decode("ascii"),
    )


def test_keyring_bootstrap_retains_legacy_and_creates_new_active_key(tmp_path, monkeypatch):
    _set_wrap_key(monkeypatch)
    path = tmp_path / "keyring.v1"
    legacy = b"L" * 32

    ring = create_initial_keyring(legacy, path, source="environment")
    active_id, active_key = get_active_key(ring)

    assert ring["schema_version"] == 1
    assert active_id == ring["active_key_id"]
    assert len(active_id) == 32
    assert len(active_key) == 32
    assert active_key != legacy
    legacy_ids = [
        key_id for key_id, entry in ring["keys"].items()
        if entry["state"] == "legacy"
    ]
    assert len(legacy_ids) == 1
    assert get_key(ring, legacy_ids[0]) == legacy
    assert load_keyring(path, source="environment") == ring
    assert b"L" * 32 not in path.read_bytes()


def test_keyring_is_not_overwritten_during_bootstrap(tmp_path, monkeypatch):
    _set_wrap_key(monkeypatch)
    path = tmp_path / "keyring.v1"
    create_initial_keyring(b"A" * 32, path, source="environment")
    original = path.read_bytes()

    with pytest.raises(KeyringError, match="already exists"):
        create_initial_keyring(b"B" * 32, path, source="environment")

    assert path.read_bytes() == original


def test_keyring_wrong_unlock_key_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "keyring.v1"
    _set_wrap_key(monkeypatch, b"A" * 32)
    create_initial_keyring(b"L" * 32, path, source="environment")

    _set_wrap_key(monkeypatch, b"B" * 32)
    with pytest.raises(KeyringError, match="authentication failed"):
        load_keyring(path, source="environment")


def test_keyring_tampering_and_truncation_fail_closed(tmp_path, monkeypatch):
    _set_wrap_key(monkeypatch)
    path = tmp_path / "keyring.v1"
    create_initial_keyring(b"L" * 32, path, source="environment")
    original = path.read_bytes()

    damaged = bytearray(original)
    damaged[-1] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(KeyringError):
        load_keyring(path, source="environment")

    path.write_bytes(original[:10])
    with pytest.raises(KeyringError):
        load_keyring(path, source="environment")


def test_keyring_rejects_invalid_schema_active_reference_and_key_material():
    ring = {
        "schema_version": 1,
        "active_key_id": "a" * 32,
        "keys": {
            "a" * 32: {
                "key": base64.b64encode(b"K" * 32).decode("ascii"),
                "created_at": "2026-01-01T00:00:00+00:00",
                "state": "active",
            }
        },
    }
    assert validate_keyring(ring) == ring

    malformed = copy.deepcopy(ring)
    malformed["schema_version"] = True
    with pytest.raises(KeyringError):
        validate_keyring(malformed)

    malformed = copy.deepcopy(ring)
    malformed["active_key_id"] = "missing"
    with pytest.raises(KeyringError):
        validate_keyring(malformed)

    malformed = copy.deepcopy(ring)
    malformed["keys"]["a" * 32]["key"] = base64.b64encode(b"short").decode("ascii")
    with pytest.raises(KeyringError):
        validate_keyring(malformed)

    malformed = copy.deepcopy(ring)
    malformed["keys"]["a" * 32]["state"] = []
    with pytest.raises(KeyringError):
        validate_keyring(malformed)


def test_keyring_save_rejects_invalid_active_state_without_replacing_file(tmp_path, monkeypatch):
    _set_wrap_key(monkeypatch)
    path = tmp_path / "keyring.v1"
    ring = create_initial_keyring(b"L" * 32, path, source="environment")
    original = path.read_bytes()
    invalid = copy.deepcopy(ring)
    invalid["keys"][invalid["active_key_id"]]["state"] = "retained"

    with pytest.raises(KeyringError):
        save_keyring(invalid, path, source="environment")

    assert path.read_bytes() == original


def test_missing_keyring_does_not_initialize_an_empty_keyring(tmp_path, monkeypatch):
    _set_wrap_key(monkeypatch)
    with pytest.raises(KeyringError, match="unavailable"):
        load_keyring(tmp_path / "missing.v1", source="environment")


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows DPAPI only")
def test_windows_dpapi_keyring_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICEASSISTANT_DATA_KEY", raising=False)
    path = tmp_path / "keyring.v1"
    ring = create_initial_keyring(b"D" * 32, path, source="windows-dpapi")
    assert load_keyring(path, source="windows-dpapi") == ring
