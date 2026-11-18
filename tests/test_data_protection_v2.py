"""VAENC2 envelope tests and VAENC1 compatibility coverage."""
from __future__ import annotations

import base64
import copy
import os

import pytest

from agent.data_protection import (
    DataProtectionError,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted_bytes,
)
from agent import keyring


def _set_key(monkeypatch, value=b"K" * 32):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(value).decode("ascii"),
    )


def _keyring_path(monkeypatch, tmp_path):
    path = tmp_path / "history" / ".voiceassistant_keyring.v1"
    monkeypatch.setattr(keyring, "KEYRING_FILE", path)
    return path


def test_without_explicit_keyring_writes_vaenc1_and_round_trips(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = _keyring_path(monkeypatch, tmp_path)
    assert not path.exists()

    encrypted = encrypt_bytes(b"legacy-format", "history.json")

    assert encrypted.startswith(b"VAENC1")
    assert decrypt_bytes(encrypted, "history.json") == b"legacy-format"
    assert not path.exists()


def test_explicit_keyring_bootstrap_switches_new_writes_to_vaenc2(tmp_path, monkeypatch):
    legacy_key = b"L" * 32
    _set_key(monkeypatch, legacy_key)
    path = _keyring_path(monkeypatch, tmp_path)
    ring = keyring.create_initial_keyring(legacy_key, path, source="environment")

    encrypted = encrypt_bytes(b"new-format", "history.json")
    active_id = ring["active_key_id"]

    assert encrypted.startswith(b"VAENC2")
    assert encrypted[6] == 2
    assert encrypted[7:23].hex() == active_id
    assert decrypt_bytes(encrypted, "history.json") == b"new-format"
    assert is_encrypted_bytes(encrypted)


def test_vaenc1_remains_readable_after_keyring_bootstrap(tmp_path, monkeypatch):
    legacy_key = b"L" * 32
    _set_key(monkeypatch, legacy_key)
    path = _keyring_path(monkeypatch, tmp_path)

    legacy_ciphertext = encrypt_bytes(b"old-data", "conversation_history.json")
    assert legacy_ciphertext.startswith(b"VAENC1")

    keyring.create_initial_keyring(legacy_key, path, source="environment")

    assert decrypt_bytes(legacy_ciphertext, "conversation_history.json") == b"old-data"


def test_vaenc2_old_key_remains_readable_after_active_key_changes(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = _keyring_path(monkeypatch, tmp_path)
    ring = keyring.create_initial_keyring(b"K" * 32, path, source="environment")
    old_ciphertext = encrypt_bytes(b"before-switch", "store.bin")

    old_active_id = ring["active_key_id"]
    next_id = os.urandom(16).hex()
    ring["keys"][old_active_id]["state"] = "retained"
    ring["keys"][next_id] = {
        "key": base64.b64encode(b"N" * 32).decode("ascii"),
        "created_at": "2026-11-18T00:00:00+00:00",
        "state": "active",
    }
    ring["active_key_id"] = next_id
    keyring.save_keyring(ring, path, source="environment")

    new_ciphertext = encrypt_bytes(b"after-switch", "store.bin")
    assert new_ciphertext[7:23].hex() == next_id
    assert decrypt_bytes(new_ciphertext, "store.bin") == b"after-switch"
    assert decrypt_bytes(old_ciphertext, "store.bin") == b"before-switch"


def test_vaenc2_rejects_unknown_key_id_and_missing_keyring(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = _keyring_path(monkeypatch, tmp_path)
    keyring.create_initial_keyring(b"K" * 32, path, source="environment")
    encrypted = encrypt_bytes(b"secret", "store.bin")

    changed_id = bytearray(encrypted)
    changed_id[7] ^= 0x80
    with pytest.raises(DataProtectionError, match="key"):
        decrypt_bytes(bytes(changed_id), "store.bin")

    path.unlink()
    with pytest.raises(DataProtectionError, match="keyring"):
        decrypt_bytes(encrypted, "store.bin")


def test_vaenc2_tampering_truncation_and_context_mismatch_fail_closed(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = _keyring_path(monkeypatch, tmp_path)
    keyring.create_initial_keyring(b"K" * 32, path, source="environment")
    encrypted = encrypt_bytes(b"authenticated", "history.json")

    tampered = bytearray(encrypted)
    tampered[-1] ^= 1
    with pytest.raises(DataProtectionError):
        decrypt_bytes(bytes(tampered), "history.json")

    with pytest.raises(DataProtectionError, match="context|authentication"):
        decrypt_bytes(encrypted, "policy.json")

    with pytest.raises(DataProtectionError, match="truncated"):
        decrypt_bytes(encrypted[:30], "history.json")


def test_vaenc2_unknown_version_and_non_encrypted_payload_fail_closed(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = _keyring_path(monkeypatch, tmp_path)
    keyring.create_initial_keyring(b"K" * 32, path, source="environment")
    encrypted = bytearray(encrypt_bytes(b"secret", "store.bin"))
    encrypted[6] = 99

    with pytest.raises(DataProtectionError, match="version"):
        decrypt_bytes(bytes(encrypted), "store.bin")
    with pytest.raises(DataProtectionError, match="supported"):
        decrypt_bytes(b"plaintext", "store.bin")
