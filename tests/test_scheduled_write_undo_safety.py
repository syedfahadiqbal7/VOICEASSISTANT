"""Regression tests for scheduled file-write undo safety."""
from __future__ import annotations

from pathlib import Path

from agent.conversation_manager import ConversationManager
from agent import undo_manager as undo_module


class _Policy:
    role = "operator"

    def evaluate_tool_call(self, *_args, **_kwargs):
        return {"allowed": True}

    def is_trusted_scheduled_open(self, _target):
        return False


class _Tools:
    def __init__(self, target: Path):
        self.target = target
        self.calls = 0

    def get_permissions(self, _name):
        return {}

    def execute(self, _name, args):
        self.calls += 1
        self.target.write_text(args["content"], encoding="utf-8")
        return {"content": "written", "is_error": False}


class _Undo:
    def __init__(self, *, snapshot="snapshot", snapshot_error=None, record_error=None, rollback_error=None):
        self.snapshot = snapshot
        self.snapshot_error = snapshot_error
        self.record_error = record_error
        self.rollback_error = rollback_error
        self.rollback_calls = 0

    def snapshot_before_write(self, _path):
        if self.snapshot_error:
            raise self.snapshot_error
        return self.snapshot

    def record_write(self, *_args):
        if self.record_error:
            raise self.record_error

    def rollback_unrecorded_write(self, _path, _backup):
        self.rollback_calls += 1
        if self.rollback_error:
            raise self.rollback_error
        return {"ok": True, "message": "target restored"}


def _manager(target, undo, logs):
    manager = ConversationManager.__new__(ConversationManager)
    manager.policy = _Policy()
    manager.tool_registry = _Tools(target)
    manager.undo = undo
    manager._log = lambda message, **_kwargs: logs.append(message)
    manager.on_security_gate = lambda _display, callback, _risk: callback()
    manager.on_speak = lambda _message: None
    manager.on_status = lambda *_args: None
    return manager


def test_scheduled_write_is_blocked_when_undo_snapshot_fails(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("before", encoding="utf-8")
    tools = _Tools(target)
    undo = _Undo(snapshot_error=OSError("disk full"))
    logs = []
    manager = _manager(target, undo, logs)
    manager.tool_registry = tools

    result = manager._run_tool_direct(
        "write_developer_code",
        {"filepath": str(target), "content": "after"},
        "Scheduled write",
        auto_approve=False,
    )

    assert result is False
    assert tools.calls == 0
    assert target.read_text(encoding="utf-8") == "before"
    assert any("File write blocked because undo protection failed" in item for item in logs)


def test_scheduled_write_rolls_back_when_undo_entry_cannot_be_saved(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("before", encoding="utf-8")
    logs = []
    undo = _Undo(record_error=OSError("undo journal unavailable"))
    manager = _manager(target, undo, logs)

    def execute(_name, args):
        target.write_text(args["content"], encoding="utf-8")
        return {"content": "written", "is_error": False}

    manager.tool_registry.execute = execute
    result = manager._run_tool_direct(
        "write_developer_code",
        {"filepath": str(target), "content": "after"},
        "Scheduled write",
        auto_approve=False,
    )

    assert result is False
    assert undo.rollback_calls == 1
    assert any("File write was rolled back" in item for item in logs)


def test_scheduled_write_surfaces_rollback_failure(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("before", encoding="utf-8")
    logs = []
    undo = _Undo(
        record_error=OSError("undo journal unavailable"),
        rollback_error=OSError("snapshot unreadable"),
    )
    manager = _manager(target, undo, logs)

    def execute(_name, args):
        target.write_text(args["content"], encoding="utf-8")
        return {"content": "written", "is_error": False}

    manager.tool_registry.execute = execute
    result = manager._run_tool_direct(
        "write_developer_code",
        {"filepath": str(target), "content": "after"},
        "Scheduled write",
        auto_approve=False,
    )

    assert result is False
    assert target.read_text(encoding="utf-8") == "after"
    assert any("CRITICAL:" in item and "rollback failed" in item for item in logs)


def test_undo_manager_restores_existing_file_after_unrecorded_write(tmp_path, monkeypatch):
    undo_root = tmp_path / "undo_backups"
    undo_root.mkdir()
    monkeypatch.setattr(undo_module, "UNDO_DIR", undo_root)
    monkeypatch.setattr(undo_module, "UNDO_LOG", tmp_path / "undo_stack.json")
    target = tmp_path / "target.txt"
    target.write_text("after", encoding="utf-8")
    backup = undo_root / "snapshot.bin"
    backup.write_bytes(b"encrypted snapshot")
    monkeypatch.setattr(undo_module, "load_encrypted_bytes", lambda _path: b"before")

    manager = undo_module.UndoManager()
    manager._save = lambda: None
    result = manager.rollback_unrecorded_write(str(target), str(backup))

    assert result["ok"] is True
    assert target.read_bytes() == b"before"
    assert not backup.exists()


def test_undo_manager_removes_new_file_only_with_valid_marker(tmp_path, monkeypatch):
    undo_root = tmp_path / "undo_backups"
    undo_root.mkdir()
    monkeypatch.setattr(undo_module, "UNDO_DIR", undo_root)
    monkeypatch.setattr(undo_module, "UNDO_LOG", tmp_path / "undo_stack.json")
    target = tmp_path / "new.txt"
    target.write_text("created", encoding="utf-8")
    marker = undo_root / "snapshot.new"
    marker.write_text("NEW_FILE", encoding="utf-8")

    manager = undo_module.UndoManager()
    manager._save = lambda: None
    result = manager.rollback_unrecorded_write(str(target), str(marker))

    assert result["ok"] is True
    assert not target.exists()
    assert not marker.exists()



def _undo_manager_for_test(tmp_path, monkeypatch):
    undo_root = tmp_path / "undo_backups"
    undo_root.mkdir()
    monkeypatch.setattr(undo_module, "UNDO_DIR", undo_root)
    monkeypatch.setattr(undo_module, "UNDO_LOG", tmp_path / "undo_stack.json")
    manager = undo_module.UndoManager()
    manager._save = lambda: None
    return manager, undo_root


def test_undo_last_retains_entry_when_snapshot_decryption_fails(tmp_path, monkeypatch):
    manager, undo_root = _undo_manager_for_test(tmp_path, monkeypatch)
    target = tmp_path / "target.txt"
    target.write_text("after", encoding="utf-8")
    backup = undo_root / "snapshot.bin"
    backup.write_bytes(b"encrypted")
    manager.stack = [{"type": "file_write", "filepath": str(target), "backup_path": str(backup)}]
    monkeypatch.setattr(
        undo_module,
        "load_encrypted_bytes",
        lambda _path: (_ for _ in ()).throw(ValueError("authentication failed")),
    )

    result = manager.undo_last()

    assert result["ok"] is False
    assert len(manager.stack) == 1
    assert target.read_text(encoding="utf-8") == "after"
    assert backup.exists()


def test_undo_last_verifies_restore_before_consuming_entry(tmp_path, monkeypatch):
    manager, undo_root = _undo_manager_for_test(tmp_path, monkeypatch)
    target = tmp_path / "target.txt"
    target.write_text("after", encoding="utf-8")
    backup = undo_root / "snapshot.bin"
    backup.write_bytes(b"encrypted")
    manager.stack = [{"type": "file_write", "filepath": str(target), "backup_path": str(backup)}]
    monkeypatch.setattr(undo_module, "load_encrypted_bytes", lambda _path: b"before")

    result = manager.undo_last()

    assert result["ok"] is True
    assert target.read_bytes() == b"before"
    assert manager.stack == []
    assert not backup.exists()


def test_undo_last_preserves_nonempty_new_folder_and_entry(tmp_path, monkeypatch):
    manager, undo_root = _undo_manager_for_test(tmp_path, monkeypatch)
    target = tmp_path / "created-folder"
    target.mkdir()
    child = target / "keep.txt"
    child.write_text("keep", encoding="utf-8")
    marker = undo_root / "snapshot.new"
    marker.write_text("NEW_FILE", encoding="utf-8")
    manager.stack = [{"type": "file_write", "filepath": str(target), "backup_path": str(marker)}]

    result = manager.undo_last()

    assert result["ok"] is False
    assert len(manager.stack) == 1
    assert child.read_text(encoding="utf-8") == "keep"
    assert marker.exists()


def test_undo_last_removes_new_file_and_consumes_entry(tmp_path, monkeypatch):
    manager, undo_root = _undo_manager_for_test(tmp_path, monkeypatch)
    target = tmp_path / "created.txt"
    target.write_text("new", encoding="utf-8")
    marker = undo_root / "snapshot.new"
    marker.write_text("NEW_FILE", encoding="utf-8")
    manager.stack = [{"type": "file_write", "filepath": str(target), "backup_path": str(marker)}]

    result = manager.undo_last()

    assert result["ok"] is True
    assert not target.exists()
    assert manager.stack == []
    assert not marker.exists()


def test_undo_last_rejects_snapshot_outside_managed_directory(tmp_path, monkeypatch):
    manager, _undo_root = _undo_manager_for_test(tmp_path, monkeypatch)
    target = tmp_path / "target.txt"
    target.write_text("after", encoding="utf-8")
    external = tmp_path / "external.snapshot"
    external.write_bytes(b"encrypted")
    manager.stack = [{"type": "file_write", "filepath": str(target), "backup_path": str(external)}]

    result = manager.undo_last()

    assert result["ok"] is False
    assert "outside the configured undo directory" in result["message"]
    assert len(manager.stack) == 1
    assert target.read_text(encoding="utf-8") == "after"
    assert external.exists()
