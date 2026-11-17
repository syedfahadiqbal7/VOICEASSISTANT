"""Regression tests for authenticated local-data encryption and migration."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from agent.data_protection import DataProtectionError, decrypt_bytes, encrypt_bytes, is_encrypted_bytes
from agent.storage import atomic_write_encrypted_bytes, atomic_write_json, load_encrypted_bytes, load_json


def _set_key(monkeypatch, value: bytes = b"V" * 32):
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", base64.urlsafe_b64encode(value).decode("ascii"))


def test_encrypted_json_round_trip_and_no_plaintext_leak(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = tmp_path / "conversation_history.json"
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    atomic_write_json(path, {"messages": [{"content": secret}]}, encrypt=True)
    raw = path.read_bytes()
    assert is_encrypted_bytes(raw)
    assert secret.encode() not in raw
    assert load_json(path)["messages"][0]["content"] == secret


def test_plaintext_json_is_migrated_on_first_encrypted_read(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = tmp_path / "agent_memory.json"
    original = {"facts": ["private local note"], "version": 1}
    path.write_text(json.dumps(original), encoding="utf-8")
    assert load_json(path, encrypt=True) == original
    assert is_encrypted_bytes(path.read_bytes())


def test_encrypted_payload_detects_tampering(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = tmp_path / "audit_log.json"
    atomic_write_json(path, [{"action": "safe"}], encrypt=True)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0x01
    path.write_bytes(raw)
    with pytest.raises(DataProtectionError):
        load_json(path)


def test_encrypted_payload_is_bound_to_store_name(monkeypatch):
    _set_key(monkeypatch)
    encrypted = encrypt_bytes(b'{"ok":true}', "history.json")
    with pytest.raises(DataProtectionError):
        decrypt_bytes(encrypted, "policy.json")


def test_wrong_key_fails_closed(monkeypatch):
    _set_key(monkeypatch, b"A" * 32)
    encrypted = encrypt_bytes(b"private", "same-context")
    _set_key(monkeypatch, b"B" * 32)
    with pytest.raises(DataProtectionError):
        decrypt_bytes(encrypted, "same-context")


def test_undo_backup_bytes_round_trip_and_legacy_migration(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    path = tmp_path / "undo_snapshot.bin"
    secret = b"private file content"
    atomic_write_encrypted_bytes(path, secret)
    assert secret not in path.read_bytes()
    assert load_encrypted_bytes(path) == secret

    legacy = tmp_path / "legacy_snapshot.bin"
    legacy.write_bytes(secret)
    assert load_encrypted_bytes(legacy) == secret
    assert is_encrypted_bytes(legacy.read_bytes())


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows DPAPI only")
def test_windows_dpapi_key_file_round_trip(tmp_path, monkeypatch):
    import agent.data_protection as protection

    monkeypatch.delenv("VOICEASSISTANT_DATA_KEY", raising=False)
    monkeypatch.setattr(protection, "DATA_KEY_FILE", tmp_path / "test.dpapi")
    encrypted = protection.encrypt_bytes(b"dpapi-protected", "dpapi-test")
    assert protection.DATA_KEY_FILE.is_file()
    assert protection.decrypt_bytes(encrypted, "dpapi-test") == b"dpapi-protected"
