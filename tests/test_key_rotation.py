"""Regression tests for local-only protected data-key administration."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from agent import keyring
from agent.backup_manager import BackupManager
from agent.data_protection import decrypt_bytes, encrypt_bytes
from agent.key_rotation import (
    DEFAULT_BACKUP_MAX_AGE_SECONDS,
    KeyRotationError,
    bootstrap_keyring,
    key_status,
    rotate_data_key,
)


PASSPHRASE = "correct horse battery staple"


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "voiceassistant"
    history = root / "history"
    history.mkdir(parents=True)
    legacy = b"Z" * 32
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(legacy).decode("ascii"),
    )
    monkeypatch.setattr(keyring, "KEYRING_FILE", history / ".voiceassistant_keyring.v1")
    store = history / "agent_memory.json"
    store.write_bytes(encrypt_bytes(b'{"memory":["keep me"]}', store.name))
    backup = tmp_path / "verified.vabackup"
    result = BackupManager(root).create_backup(backup, PASSPHRASE)
    assert result["ok"] is True
    return root, history, store, backup, legacy


def test_status_reports_legacy_mode_without_creating_keyring(tmp_path, monkeypatch):
    root, history, _store, _backup, _legacy = _setup(tmp_path, monkeypatch)
    status = key_status(root)
    assert status == {
        "keyring_present": False,
        "mode": "legacy_single_key",
        "active_key_id": None,
        "retained_keys": 0,
        "legacy_key_retained": False,
    }
    assert not (history / ".voiceassistant_keyring.v1").exists()


def test_bootstrap_requires_confirmation_and_verified_recent_backup(tmp_path, monkeypatch):
    root, _history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        bootstrap_keyring(root, backup, PASSPHRASE)
    with pytest.raises(KeyRotationError, match="verification failed"):
        bootstrap_keyring(root, backup, "wrong passphrase", confirm=True)

    result = bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)
    assert result["ok"] is True
    assert result["legacy_key_retained"] is True
    assert key_status(root)["keyring_present"] is True


def test_bootstrap_rejects_stale_backup(tmp_path, monkeypatch):
    root, _history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    os.utime(backup, (1, 1))
    with pytest.raises(KeyRotationError, match="too old"):
        bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)


def test_rotation_switches_active_key_and_retains_all_old_keys_without_rewriting_files(tmp_path, monkeypatch):
    root, history, store, backup, legacy = _setup(tmp_path, monkeypatch)
    original_v1 = store.read_bytes()
    bootstrap = bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)
    ring_before = keyring.load_keyring()
    old_active = ring_before["active_key_id"]
    old_v2 = encrypt_bytes(b"old-active-ciphertext", "old.bin")

    result = rotate_data_key(root, backup, PASSPHRASE, confirm=True)
    ring_after = keyring.load_keyring()
    assert result["ok"] is True
    assert result["existing_files_rewritten"] == 0
    assert result["active_key_id"] != old_active
    assert ring_after["keys"][old_active]["state"] == "retained"
    assert any(entry["state"] == "legacy" for entry in ring_after["keys"].values())
    assert decrypt_bytes(original_v1, store.name) == b'{"memory":["keep me"]}'
    assert decrypt_bytes(old_v2, "old.bin") == b"old-active-ciphertext"
    assert store.read_bytes() == original_v1

    new_v2 = encrypt_bytes(b"new-active-ciphertext", "new.bin")
    assert new_v2[7:23].hex() == result["active_key_id"]
    assert decrypt_bytes(new_v2, "new.bin") == b"new-active-ciphertext"

    audit = decrypt_bytes((history / "audit_log.json").read_bytes(), "audit_log.json")
    events = json.loads(audit.decode("utf-8"))
    names = [event["event"] for event in events]
    assert "data_key.bootstrap_started" in names
    assert "data_key.bootstrap_completed" in names
    assert "data_key.rotation_started" in names
    assert "data_key.rotation_completed" in names
    assert legacy not in (history / "audit_log.json").read_bytes()


def test_rotation_requires_confirmation_and_existing_keyring(tmp_path, monkeypatch):
    root, _history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        rotate_data_key(root, backup, PASSPHRASE)
    with pytest.raises(KeyRotationError, match="bootstrap"):
        rotate_data_key(root, backup, PASSPHRASE, confirm=True)


def test_rotation_refuses_corrupt_or_stale_backup_without_switching_key(tmp_path, monkeypatch):
    root, _history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)
    active_before = keyring.load_keyring()["active_key_id"]

    backup.write_bytes(b"not a backup")
    with pytest.raises(KeyRotationError, match="verification failed"):
        rotate_data_key(root, backup, PASSPHRASE, confirm=True)
    assert keyring.load_keyring()["active_key_id"] == active_before

    BackupManager(root).create_backup(backup, PASSPHRASE, overwrite=True)
    os.utime(backup, (1, 1))
    with pytest.raises(KeyRotationError, match="too old"):
        rotate_data_key(root, backup, PASSPHRASE, confirm=True)
    assert keyring.load_keyring()["active_key_id"] == active_before


def test_rotation_refuses_existing_shared_admin_lock(tmp_path, monkeypatch):
    root, history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)
    lock = history / ".voiceassistant_key_admin.lock"
    lock.write_text('{"pid":999}', encoding="utf-8")
    active_before = keyring.load_keyring()["active_key_id"]

    with pytest.raises(KeyRotationError, match="lock"):
        rotate_data_key(root, backup, PASSPHRASE, confirm=True)
    assert keyring.load_keyring()["active_key_id"] == active_before


def test_rotation_audit_failure_before_switch_aborts(tmp_path, monkeypatch):
    import agent.key_rotation as rotation

    root, _history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)
    active_before = keyring.load_keyring()["active_key_id"]

    def fail_audit(*_args, **_kwargs):
        raise OSError("audit storage unavailable")

    monkeypatch.setattr(rotation, "_append_audit", fail_audit)
    with pytest.raises(OSError, match="audit storage"):
        rotate_data_key(root, backup, PASSPHRASE, confirm=True)
    assert keyring.load_keyring()["active_key_id"] == active_before


def test_repeated_rotation_retains_every_previous_dek(tmp_path, monkeypatch):
    root, _history, _store, backup, _legacy = _setup(tmp_path, monkeypatch)
    bootstrap_keyring(root, backup, PASSPHRASE, confirm=True)
    ids = [keyring.load_keyring()["active_key_id"]]
    for _ in range(2):
        BackupManager(root).create_backup(backup, PASSPHRASE, overwrite=True)
        result = rotate_data_key(root, backup, PASSPHRASE, confirm=True)
        ids.append(result["active_key_id"])
    ring = keyring.load_keyring()
    assert len(set(ids)) == 3
    assert ring["active_key_id"] == ids[-1]
    assert all(ring["keys"][key_id]["state"] == "retained" for key_id in ids[:-1])
