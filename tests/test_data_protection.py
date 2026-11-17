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


def test_history_memory_policy_and_scheduler_stores_encrypt_at_rest(tmp_path, monkeypatch):
    _set_key(monkeypatch)

    from agent.history_manager import HistoryManager
    import agent.scheduler as scheduler_module
    from agent.memory_store import MemoryStore
    from agent.policy import PolicyEngine

    history = HistoryManager()
    history.history_file = tmp_path / "conversation_history.json"
    history.audit_file = tmp_path / "audit_log.json"
    history.save_conversation([{"role": "user", "content": "private conversation"}])
    history.log_audit("write", "private path", True, "private result")
    assert is_encrypted_bytes(history.history_file.read_bytes())
    assert is_encrypted_bytes(history.audit_file.read_bytes())
    assert history.load_conversation()[0]["content"] == "private conversation"
    assert history.get_audit_log()[0]["details"] == "private path"

    memory = MemoryStore(tmp_path / "agent_memory.json")
    memory.add_fact("private remembered fact")
    assert is_encrypted_bytes(memory.path.read_bytes())
    assert "private remembered fact" not in memory.path.read_text(encoding="latin-1")
    assert MemoryStore(memory.path).context_snippet().find("private remembered fact") >= 0

    policy_path = tmp_path / "agent_policy.json"
    policy = PolicyEngine(policy_path)
    policy.data["role"] = "read_only"
    policy.save()
    assert is_encrypted_bytes(policy_path.read_bytes())
    assert PolicyEngine(policy_path).role == "read_only"

    schedule_path = tmp_path / "scheduled_jobs.json"
    monkeypatch.setattr(scheduler_module, "SCHEDULE_FILE", schedule_path)
    scheduler = scheduler_module.JobScheduler()
    from datetime import datetime, timedelta
    scheduler.add_once("private scheduled prompt", datetime.now() + timedelta(hours=1))
    assert is_encrypted_bytes(schedule_path.read_bytes())
    assert scheduler_module.JobScheduler().list_jobs()[0]["prompt"] == "private scheduled prompt"


def test_store_authentication_failure_is_never_treated_as_empty_history_or_memory(tmp_path, monkeypatch):
    _set_key(monkeypatch, b"A" * 32)
    from agent.history_manager import HistoryManager
    from agent.memory_store import MemoryStore

    history_path = tmp_path / "conversation_history.json"
    atomic_write_json(history_path, {"messages": [{"content": "do not lose me"}]}, encrypt=True)
    raw = bytearray(history_path.read_bytes())
    raw[-1] ^= 0x01
    history_path.write_bytes(raw)
    history = HistoryManager()
    history.history_file = history_path
    with pytest.raises(DataProtectionError):
        history.load_conversation()

    memory_path = tmp_path / "agent_memory.json"
    atomic_write_json(memory_path, {"facts": ["do not lose me"]}, encrypt=True)
    raw = bytearray(memory_path.read_bytes())
    raw[-1] ^= 0x01
    memory_path.write_bytes(raw)
    with pytest.raises(DataProtectionError):
        MemoryStore(memory_path)


def test_undo_snapshots_and_stack_are_encrypted_and_restore_exact_bytes(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    import agent.undo_manager as undo_module

    monkeypatch.setattr(undo_module, "UNDO_DIR", tmp_path / "undo_backups")
    monkeypatch.setattr(undo_module, "UNDO_LOG", tmp_path / "undo_stack.json")
    undo = undo_module.UndoManager()
    target = tmp_path / "sensitive.txt"
    original = b"private original bytes\x00\xff"
    target.write_bytes(original)
    backup = undo.snapshot_before_write(str(target))
    assert backup is not None
    assert is_encrypted_bytes(Path(backup).read_bytes())
    target.write_bytes(b"modified")
    undo.record_write(str(target), backup)
    assert is_encrypted_bytes(undo_module.UNDO_LOG.read_bytes())
    result = undo.undo_last()
    assert result["ok"] is True
    assert target.read_bytes() == original


def test_undo_and_scheduler_authentication_fail_closed(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    import agent.undo_manager as undo_module
    import agent.scheduler as scheduler_module

    undo_log = tmp_path / "undo_stack.json"
    atomic_write_json(undo_log, [{"type": "file_write"}], encrypt=True)
    raw = bytearray(undo_log.read_bytes())
    raw[-1] ^= 0x01
    undo_log.write_bytes(raw)
    monkeypatch.setattr(undo_module, "UNDO_LOG", undo_log)
    with pytest.raises(DataProtectionError):
        undo_module.UndoManager()

    schedule_path = tmp_path / "scheduled_jobs.json"
    atomic_write_json(schedule_path, [{"id": "job-1"}], encrypt=True)
    raw = bytearray(schedule_path.read_bytes())
    raw[-1] ^= 0x01
    schedule_path.write_bytes(raw)
    monkeypatch.setattr(scheduler_module, "SCHEDULE_FILE", schedule_path)
    with pytest.raises(DataProtectionError):
        scheduler_module.JobScheduler()


def test_custom_skill_manifests_and_packs_are_encrypted_and_source_has_no_private_actions(tmp_path, monkeypatch):
    _set_key(monkeypatch)
    import agent.skill_author as author
    import agent.skill_packs as packs

    monkeypatch.setattr(author, "BASE_DIR", tmp_path)
    private_trigger = "open confidential internal portal"
    private_url = "https://internal.example.test/private?token=super-secret-value"
    skill = {
        "skill_id": "private_portal",
        "title": "Private Portal",
        "description": "A private custom workflow",
        "triggers": [private_trigger],
        "actions": [{"tool": "browser_open", "url": private_url}],
        "samples": [private_trigger],
    }
    result = author.install_skill(skill, base_dir=tmp_path)
    assert result["ok"] is True

    manifest_path = tmp_path / "history" / "custom_skills" / "private_portal.json"
    index_path = tmp_path / "history" / "custom_skills" / "index.json"
    source_path = tmp_path / "plugins" / "private_portal_skill.py"
    assert is_encrypted_bytes(manifest_path.read_bytes())
    assert is_encrypted_bytes(index_path.read_bytes())
    source = source_path.read_text(encoding="utf-8")
    compile(source, str(source_path), "exec")
    assert private_trigger not in source
    assert private_url not in source
    assert "super-secret-value" not in source

    loaded = author.load_all_custom_manifests(base_dir=tmp_path)
    assert loaded[0]["actions"][0]["url"] == private_url

    monkeypatch.setattr(packs, "PACKS_FILE", tmp_path / "history" / "skill_packs.json")
    manager = packs.SkillPackManager(path=packs.PACKS_FILE)
    assert is_encrypted_bytes(packs.PACKS_FILE.read_bytes())
    assert manager.is_enabled("private_portal")
