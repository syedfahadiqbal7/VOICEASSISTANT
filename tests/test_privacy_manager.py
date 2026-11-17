"""Tests for opt-in retention previews and explicit privacy purges."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from agent.data_protection import DataProtectionError, is_encrypted_bytes
from agent.privacy_manager import PrivacyManager
from agent.storage import atomic_write_json, load_json


def _key(monkeypatch, value=b"R" * 32):
    import base64
    monkeypatch.setenv("VOICEASSISTANT_DATA_KEY", base64.urlsafe_b64encode(value).decode("ascii"))


def test_retention_is_disabled_by_default_and_policy_is_encrypted(tmp_path, monkeypatch):
    _key(monkeypatch)
    manager = PrivacyManager(base_dir=tmp_path)
    assert all(manager.get_policy()[key] is None for key in (
        "conversation_days", "audit_days", "memory_days", "completed_job_days"
    ))
    manager.set_retention(audit_days=365, conversation_days=0)
    assert is_encrypted_bytes(manager.policy_path.read_bytes())
    assert manager.get_policy()["audit_days"] == 365
    assert manager.get_policy()["conversation_days"] is None


def test_retention_preview_and_purge_keep_recent_and_pending_data(tmp_path, monkeypatch):
    _key(monkeypatch)
    now = datetime.now()
    history = tmp_path / "history"
    history.mkdir()
    conversation = history / "conversation_history.json"
    memory = history / "agent_memory.json"
    audit = history / "audit_log.json"
    schedule = history / "scheduled_jobs.json"

    atomic_write_json(conversation, {"messages": [{"content": "old private chat"}]}, encrypt=True)
    atomic_write_json(memory, {"facts": ["old private fact"]}, encrypt=True)
    old_time = (now - timedelta(days=10)).timestamp()
    os.utime(conversation, (old_time, old_time))
    os.utime(memory, (old_time, old_time))

    atomic_write_json(audit, [
        {"timestamp": (now - timedelta(days=400)).isoformat(), "action": "old"},
        {"timestamp": (now - timedelta(days=2)).isoformat(), "action": "recent"},
        {"action": "unknown timestamp is retained"},
    ], encrypt=True)
    atomic_write_json(schedule, [
        {"id": "old-done", "status": "completed", "last_run": (now - timedelta(days=200)).isoformat()},
        {"id": "recent-failed", "status": "failed", "last_run": (now - timedelta(days=2)).isoformat()},
        {"id": "old-pending", "status": "pending", "run_at": (now - timedelta(days=200)).isoformat()},
        {"id": "unknown-terminal", "status": "completed"},
    ], encrypt=True)

    manager = PrivacyManager(base_dir=tmp_path)
    manager.set_retention(
        conversation_days=5,
        memory_days=5,
        audit_days=365,
        completed_job_days=90,
    )
    preview = manager.preview(now=now)
    assert preview["conversation_history"]["delete_file"] is True
    assert preview["memory"]["delete_file"] is True
    assert preview["audit_log"]["delete_entries"] == 1
    assert preview["scheduled_jobs"]["delete_terminal_jobs"] == 1

    with pytest.raises(PermissionError):
        manager.purge(now=now)

    result = manager.purge(confirm=True, now=now)
    assert result["deleted"] == {
        "conversation_history_files": 1,
        "memory_files": 1,
        "audit_entries": 1,
        "scheduled_jobs": 1,
    }
    assert not conversation.exists()
    assert not memory.exists()
    assert [entry["action"] for entry in load_json(audit)] == ["recent", "unknown timestamp is retained"]
    remaining = load_json(schedule)
    assert {job["id"] for job in remaining} == {"recent-failed", "old-pending", "unknown-terminal"}


def test_tampered_retention_policy_fails_closed(tmp_path, monkeypatch):
    _key(monkeypatch)
    manager = PrivacyManager(base_dir=tmp_path)
    manager.set_retention(audit_days=30)
    raw = bytearray(manager.policy_path.read_bytes())
    raw[-1] ^= 0x01
    manager.policy_path.write_bytes(raw)
    with pytest.raises(DataProtectionError):
        PrivacyManager(base_dir=tmp_path)


def test_invalid_retention_values_are_rejected_without_changing_policy(tmp_path, monkeypatch):
    _key(monkeypatch)
    manager = PrivacyManager(base_dir=tmp_path)
    before = manager.get_policy()
    for invalid in (-1, True, 36501, "30"):
        with pytest.raises(ValueError):
            manager.set_retention(audit_days=invalid)
    assert manager.get_policy() == before
