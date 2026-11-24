"""Portable-backup coverage and restore regressions."""
from __future__ import annotations

import base64
import zipfile

from agent.backup_manager import BackupManager
from agent.data_protection import is_encrypted_bytes
from agent.privacy_manager import PrivacyManager
from agent.storage import atomic_write_json, load_json


def _key(monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"B" * 32).decode("ascii"),
    )


def test_data_retention_policy_is_included_verified_and_restored(tmp_path, monkeypatch):
    _key(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    privacy = PrivacyManager(base_dir=source)
    privacy.set_retention(
        conversation_days=90,
        audit_days=365,
        memory_days=180,
        profile_days=365,
        execution_history_days=30,
        completed_job_days=30,
    )
    policy_path = source / "history" / "data_retention.json"
    assert is_encrypted_bytes(policy_path.read_bytes())
    atomic_write_json(source / "history" / "user_profile.json", {"version": 1, "interests": ["AI"]}, encrypt=True)
    atomic_write_json(source / "history" / "scheduled_execution_history.json", {"version": 1, "records": []}, encrypt=True)

    backup_path = tmp_path / "voiceassistant.vabackup"
    source_manager = BackupManager(source)
    created = source_manager.create_backup(backup_path, "a strong backup passphrase 2026")
    assert created["ok"] is True

    verified = source_manager.verify_backup(
        backup_path, "a strong backup passphrase 2026"
    )
    assert "data_retention.json" in verified["file_names"]
    assert "user_profile.json" in verified["file_names"]
    assert "scheduled_execution_history.json" in verified["file_names"]
    with zipfile.ZipFile(__import__("io").BytesIO(
        __import__("agent.backup_manager", fromlist=["_decrypt_archive"])._decrypt_archive(
            backup_path.read_bytes(), "a strong backup passphrase 2026"
        )
    )) as archive:
        assert "data_retention.json" in archive.namelist()
        assert "user_profile.json" in archive.namelist()
        assert "scheduled_execution_history.json" in archive.namelist()

    destination = tmp_path / "destination"
    destination.mkdir()
    result = BackupManager(destination).restore_backup(
        backup_path,
        "a strong backup passphrase 2026",
        confirm_overwrite=True,
    )
    assert "data_retention.json" in result["restored_files"]
    assert "user_profile.json" in result["restored_files"]
    assert "scheduled_execution_history.json" in result["restored_files"]
    restored = destination / "history" / "data_retention.json"
    assert is_encrypted_bytes(restored.read_bytes())
    assert load_json(restored, encrypt=True) == {
        "version": 1,
        "conversation_days": 90,
        "audit_days": 365,
        "memory_days": 180,
        "profile_days": 365,
        "execution_history_days": 30,
        "completed_job_days": 30,
    }
