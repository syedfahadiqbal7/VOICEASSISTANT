"""Resumable local key migration regression tests."""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from agent import keyring
from agent.data_protection import DataProtectionError, decrypt_bytes, encrypt_bytes
from agent.key_migration import (
    MigrationError,
    MigrationTarget,
    discover_runtime_targets,
    inspect_migration,
    migrate_to_vaenc2,
)


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "app"
    history = root / "history"
    history.mkdir(parents=True)
    legacy = b"L" * 32
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(legacy).decode("ascii"),
    )
    monkeypatch.setattr(keyring, "KEYRING_FILE", history / ".voiceassistant_keyring.v1")
    return root, history, legacy


def test_inventory_is_read_only_and_only_reports_explicit_runtime_files(tmp_path, monkeypatch):
    root, history, _legacy = _setup(tmp_path, monkeypatch)
    legacy_file = history / "conversation_history.json"
    legacy_file.write_bytes(encrypt_bytes(b'{"messages":[]}', legacy_file.name))
    plaintext = history / "notes.txt"
    plaintext.write_text("not a managed store", encoding="utf-8")
    nested = history / "custom_skills"
    nested.mkdir()
    skill = nested / "example.json"
    skill.write_bytes(encrypt_bytes(b'{"skill_id":"example"}', skill.name))

    before = legacy_file.read_bytes()
    inventory = inspect_migration(root)

    assert inventory["legacy_vaenc1"] == 2
    assert {row["path"] for row in inventory["files"]} == {
        "history/conversation_history.json", "history/custom_skills/example.json"
    }
    assert legacy_file.read_bytes() == before
    assert not (history / ".voiceassistant_migration_v1.json").exists()


def test_migration_requires_both_explicit_confirmations(tmp_path, monkeypatch):
    root, history, legacy = _setup(tmp_path, monkeypatch)
    target = history / "agent_memory.json"
    target.write_bytes(encrypt_bytes(b'{"facts":["private"]}', target.name))
    keyring.create_initial_keyring(legacy, source="environment")

    with pytest.raises(PermissionError):
        migrate_to_vaenc2(root, [MigrationTarget(target, target.name)], confirm=True)
    with pytest.raises(PermissionError):
        migrate_to_vaenc2(root, [MigrationTarget(target, target.name)], backup_verified=True)


def test_migration_rewrites_vaenc1_and_verifies_plaintext_digest(tmp_path, monkeypatch):
    root, history, legacy = _setup(tmp_path, monkeypatch)
    target = history / "conversation_history.json"
    clear = b'{"messages":[{"content":"private"}]}'
    target.write_bytes(encrypt_bytes(clear, target.name))
    original_digest = __import__("hashlib").sha256(clear).hexdigest()
    keyring.create_initial_keyring(legacy, source="environment")

    result = migrate_to_vaenc2(
        root, [MigrationTarget(target, target.name)],
        confirm=True, backup_verified=True,
    )

    assert result["complete"] is True
    assert result["migrated"] == 1
    assert target.read_bytes().startswith(b"VAENC2")
    assert decrypt_bytes(target.read_bytes(), target.name) == clear
    journal = (history / ".voiceassistant_migration_v1.json").read_text(encoding="utf-8")
    assert original_digest in journal
    assert "private" not in journal


def test_migration_is_restartable_and_idempotent_after_completed_replace(tmp_path, monkeypatch):
    root, history, legacy = _setup(tmp_path, monkeypatch)
    target = history / "audit_log.json"
    target.write_bytes(encrypt_bytes(b"[]", target.name))
    keyring.create_initial_keyring(legacy, source="environment")
    selected = [MigrationTarget(target, target.name)]

    first = migrate_to_vaenc2(root, selected, confirm=True, backup_verified=True)
    second = migrate_to_vaenc2(root, selected, confirm=True, backup_verified=True)

    assert first["migrated"] == 1
    assert second["migrated"] == 0
    assert second["already_current"] == 1
    assert second["complete"] is True


def test_migration_rejects_tampered_vaenc1_without_changing_source(tmp_path, monkeypatch):
    root, history, legacy = _setup(tmp_path, monkeypatch)
    target = history / "scheduled_jobs.json"
    payload = bytearray(encrypt_bytes(b"[]", target.name))
    payload[-1] ^= 1
    target.write_bytes(payload)
    before = target.read_bytes()
    keyring.create_initial_keyring(legacy, source="environment")

    result = migrate_to_vaenc2(
        root, [MigrationTarget(target, target.name)],
        confirm=True, backup_verified=True,
    )

    assert result["complete"] is False
    assert len(result["failed"]) == 1
    assert target.read_bytes() == before


def test_migration_rejects_plaintext_unknown_and_out_of_root_paths(tmp_path, monkeypatch):
    root, history, legacy = _setup(tmp_path, monkeypatch)
    plain = history / "plain.json"
    plain.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"VAENC1")
    keyring.create_initial_keyring(legacy, source="environment")

    refused = migrate_to_vaenc2(
        root, [MigrationTarget(plain, plain.name)],
        confirm=True, backup_verified=True,
    )
    assert refused["complete"] is False
    assert "Only VAENC1" in refused["failed"][0]["error"]
    with pytest.raises(MigrationError, match="escapes"):
        inspect_migration(root, [MigrationTarget(outside, outside.name)])


def test_migration_rejects_symlinks_and_existing_lock(tmp_path, monkeypatch):
    root, history, legacy = _setup(tmp_path, monkeypatch)
    target = history / "agent_policy.json"
    target.write_bytes(encrypt_bytes(b"{}", target.name))
    keyring.create_initial_keyring(legacy, source="environment")
    link = history / "linked.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(MigrationError, match="symbolic"):
        inspect_migration(root, [MigrationTarget(link, link.name)])

    lock = history / ".voiceassistant_migration_v1.lock"
    lock.write_text('{"pid":999}', encoding="utf-8")
    with pytest.raises(MigrationError, match="lock exists"):
        migrate_to_vaenc2(
            root, [MigrationTarget(target, target.name)],
            confirm=True, backup_verified=True,
        )
    assert target.read_bytes().startswith(b"VAENC1")


def test_discovery_excludes_new_file_undo_markers(tmp_path, monkeypatch):
    root, history, _legacy = _setup(tmp_path, monkeypatch)
    undo = history / "undo_backups"
    undo.mkdir()
    (undo / "snapshot.bin").write_bytes(b"VAENC1")
    (undo / "new-file.new").write_text("NEW_FILE", encoding="utf-8")
    targets = discover_runtime_targets(root)
    assert [item.path.name for item in targets] == ["snapshot.bin"]
