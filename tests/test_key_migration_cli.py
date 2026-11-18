"""Integration tests for the explicit backup-verified migration CLI."""
from __future__ import annotations

import base64

from agent import keyring
from agent.backup_manager import BackupManager
from agent.key_migration import inspect_migration
from agent.storage import atomic_write_json
import key_migration_cli


PASSPHRASE = "migration backup passphrase 2026"


def _install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    history = root / "history"
    history.mkdir(parents=True)
    legacy_key = b"M" * 32
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(legacy_key).decode("ascii"),
    )
    store = history / "conversation_history.json"
    atomic_write_json(store, {"messages": [{"text": "preserve me"}]}, encrypt=True)
    assert store.read_bytes().startswith(b"VAENC1")
    ring_path = history / ".voiceassistant_keyring.v1"
    monkeypatch.setattr(keyring, "KEYRING_FILE", ring_path)
    keyring.create_initial_keyring(legacy_key, ring_path, source="environment")
    monkeypatch.setattr(key_migration_cli, "BASE_DIR", root)
    backup = tmp_path / "offline" / "pre-migration.vabackup"
    backup.parent.mkdir()
    BackupManager(root).create_backup(backup, PASSPHRASE)
    monkeypatch.setattr(key_migration_cli.getpass, "getpass", lambda _prompt: PASSPHRASE)
    return root, store, backup


def test_cli_migrates_only_after_explicit_confirmation_and_backup_verification(tmp_path, monkeypatch, capsys):
    _root, store, backup = _install(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: "MIGRATE ALL REGISTERED DATA STORES")
    result = key_migration_cli.main(["migrate", "--backup", str(backup)])
    assert result == 0
    assert store.read_bytes().startswith(b"VAENC2")
    assert "migrated_verified" in capsys.readouterr().out


def test_cli_refuses_stale_backup_before_rewriting_any_store(tmp_path, monkeypatch, capsys):
    _root, store, backup = _install(tmp_path, monkeypatch)
    atomic_write_json(store, {"messages": [{"text": "changed after backup"}]}, encrypt=True)
    assert store.read_bytes().startswith(b"VAENC2")
    monkeypatch.setattr("builtins.input", lambda _prompt: "MIGRATE ALL REGISTERED DATA STORES")
    result = key_migration_cli.main(["migrate", "--backup", str(backup)])
    assert result == 1
    assert store.read_bytes().startswith(b"VAENC2")
    assert "stale or differs" in capsys.readouterr().err


def test_cli_refuses_plaintext_registered_store_before_any_migration(tmp_path, monkeypatch, capsys):
    _root, store, backup = _install(tmp_path, monkeypatch)
    store.write_text('{"messages":[]}', encoding="utf-8")
    before = store.read_bytes()
    monkeypatch.setattr("builtins.input", lambda _prompt: "MIGRATE ALL REGISTERED DATA STORES")
    result = key_migration_cli.main(["migrate", "--backup", str(backup)])
    assert result == 1
    assert store.read_bytes() == before
    assert "plaintext/unknown or unsupported envelopes" in capsys.readouterr().err
    inventory = inspect_migration(_root)
    assert inventory["plaintext_or_unknown"] == 1


def test_cli_refuses_unsupported_envelope_before_any_migration(tmp_path, monkeypatch, capsys):
    _root, store, backup = _install(tmp_path, monkeypatch)
    store.write_bytes(b"VAENC3" + b"unsupported")
    before = store.read_bytes()
    inventory = inspect_migration(_root)
    assert inventory["unsupported_envelope"] == 1
    assert inventory["plaintext_or_unknown"] == 0
    monkeypatch.setattr("builtins.input", lambda _prompt: "MIGRATE ALL REGISTERED DATA STORES")
    result = key_migration_cli.main(["migrate", "--backup", str(backup)])
    assert result == 1
    assert store.read_bytes() == before
    assert "plaintext/unknown or unsupported envelopes" in capsys.readouterr().err


def test_cli_refuses_malformed_known_envelope_version(tmp_path, monkeypatch, capsys):
    root, store, _backup = _install(tmp_path, monkeypatch)
    store.write_bytes(b"VAENC1" + b"\x02" + (b"X" * 64))
    inventory = inspect_migration(root)
    assert inventory["unsupported_envelope"] == 1
    assert inventory["legacy_vaenc1"] == 0


def test_cli_inspect_is_read_only(tmp_path, monkeypatch, capsys):
    _root, store, _backup = _install(tmp_path, monkeypatch)
    before = store.read_bytes()
    assert key_migration_cli.main(["inspect"]) == 0
    assert store.read_bytes() == before
    assert "legacy_vaenc1" in capsys.readouterr().out
