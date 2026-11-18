"""Fail-closed retained data-key retirement tests."""
from __future__ import annotations

import base64

import pytest

from agent import keyring
from agent.backup_manager import BackupManager
from agent.data_protection import encrypt_bytes
from agent.key_recovery import export_recovery_kit
from agent.key_rotation import KeyRotationError, retire_data_key
from agent.storage import atomic_write_json, load_json


BACKUP_PASSPHRASE = "a sufficiently long backup phrase"
RECOVERY_PASSPHRASE = "a sufficiently long recovery phrase"


def _prepare(tmp_path, monkeypatch, *, include_legacy=False):
    root = tmp_path / "install"
    history = root / "history"
    history.mkdir(parents=True)
    legacy_key = b"L" * 32
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(legacy_key).decode("ascii"),
    )
    if include_legacy:
        legacy_file = history / "conversation_history.json"
        legacy_file.write_bytes(encrypt_bytes(b'{"legacy":true}', legacy_file.name))
    ring_path = history / ".voiceassistant_keyring.v1"
    monkeypatch.setattr(keyring, "KEYRING_FILE", ring_path)
    ring = keyring.create_initial_keyring(legacy_key, ring_path, source="environment")
    legacy_id = next(
        key_id for key_id, entry in ring["keys"].items()
        if entry["state"] == "legacy"
    )
    atomic_write_json(history / "data_retention.json", {
        "version": 1,
        "conversation_days": None,
        "audit_days": None,
        "memory_days": None,
        "completed_job_days": None,
    }, encrypt=True)
    atomic_write_json(history / "audit_log.json", [], encrypt=True)
    return root, history, ring_path, legacy_id


def _artifacts(root, tmp_path):
    backup = tmp_path / "offline" / "voiceassistant.vabackup"
    recovery = tmp_path / "offline" / "voiceassistant.varecovery"
    backup.parent.mkdir(parents=True, exist_ok=True)
    BackupManager(root).create_backup(backup, BACKUP_PASSPHRASE)
    export_recovery_kit(root, recovery, RECOVERY_PASSPHRASE)
    return backup, recovery


def test_retirement_requires_full_migration_backup_and_exact_recovery_kit(tmp_path, monkeypatch):
    root, history, ring_path, legacy_id = _prepare(tmp_path, monkeypatch)
    backup, recovery = _artifacts(root, tmp_path)

    with pytest.raises(PermissionError):
        retire_data_key(
            root, legacy_id, backup, BACKUP_PASSPHRASE,
            recovery, RECOVERY_PASSPHRASE,
        )

    result = retire_data_key(
        root, legacy_id, backup, BACKUP_PASSPHRASE,
        recovery, RECOVERY_PASSPHRASE, confirm=True,
    )
    assert result["ok"] is True
    assert result["retired_key_id"] == legacy_id
    assert result["recovery_kit_verified"] is True
    assert result["secure_erasure_guaranteed"] is False
    persisted = keyring.load_keyring(ring_path, source="environment")
    assert legacy_id not in persisted["keys"]
    assert persisted["active_key_id"] == result["active_key_id"]
    audit = load_json(history / "audit_log.json", encrypt=True)
    assert any(row.get("event") == "data_key.retirement_completed" for row in audit)


def test_retirement_refuses_any_remaining_vaenc1_store(tmp_path, monkeypatch):
    root, _history, _ring_path, legacy_id = _prepare(
        tmp_path, monkeypatch, include_legacy=True
    )
    backup, recovery = _artifacts(root, tmp_path)
    with pytest.raises(KeyRotationError, match="VAENC2"):
        retire_data_key(
            root, legacy_id, backup, BACKUP_PASSPHRASE,
            recovery, RECOVERY_PASSPHRASE, confirm=True,
        )


def test_retirement_refuses_backup_that_does_not_match_live_store(tmp_path, monkeypatch):
    root, history, _ring_path, legacy_id = _prepare(tmp_path, monkeypatch)
    backup, recovery = _artifacts(root, tmp_path)
    atomic_write_json(history / "data_retention.json", {
        "version": 1,
        "conversation_days": 10,
        "audit_days": None,
        "memory_days": None,
        "completed_job_days": None,
    }, encrypt=True)
    with pytest.raises(KeyRotationError, match="stale or differs"):
        retire_data_key(
            root, legacy_id, backup, BACKUP_PASSPHRASE,
            recovery, RECOVERY_PASSPHRASE, confirm=True,
        )


def test_retirement_refuses_recovery_kit_from_old_keyring(tmp_path, monkeypatch):
    root, _history, ring_path, legacy_id = _prepare(tmp_path, monkeypatch)
    backup, recovery = _artifacts(root, tmp_path)
    # Rotate the keyring after kit creation; old recovery kit must not authorize retirement.
    current = keyring.load_keyring(ring_path, source="environment")
    updated = dict(current)
    updated["keys"] = dict(current["keys"])
    active_id = current["active_key_id"]
    updated["keys"][active_id] = dict(updated["keys"][active_id], state="retained")
    import os
    import base64 as b64
    new_id = os.urandom(16).hex()
    updated["keys"][new_id] = {
        "key": b64.b64encode(b"N" * 32).decode("ascii"),
        "created_at": "2026-11-18T00:00:00+00:00",
        "state": "active",
    }
    updated["active_key_id"] = new_id
    keyring.save_keyring(updated, ring_path, source="environment")
    with pytest.raises(KeyRotationError, match="exact current keyring"):
        retire_data_key(
            root, legacy_id, backup, BACKUP_PASSPHRASE,
            recovery, RECOVERY_PASSPHRASE, confirm=True,
        )
