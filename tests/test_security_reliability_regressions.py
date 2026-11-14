"""Focused regression tests for the security/reliability hardening changes."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent.policy import PolicyEngine
from agent.verifier import Verifier
from mobile_api.bridge import MobileBridge
from tools.powershell_executor import PowerShellExecutorTool


def test_policy_rejects_path_that_only_contains_allowed_folder_name(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / "Desktop").mkdir(parents=True)
    engine = PolicyEngine(path=tmp_path / "policy.json")
    engine.data["allowed_write_roots"] = ["Desktop"]

    allowed, _ = engine.evaluate_write_path(str(home / "Desktop" / "ok.txt"))
    rejected, reason = engine.evaluate_write_path(str(tmp_path / "Desktop" / "spoof.txt"))

    assert allowed is True
    assert rejected is False
    assert "outside canonical" in reason


def test_policy_rejects_symlink_escape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = desktop / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this platform")

    engine = PolicyEngine(path=tmp_path / "policy.json")
    engine.data["allowed_write_roots"] = ["Desktop"]
    allowed, reason = engine.evaluate_write_path(str(link / "payload.txt"))

    assert allowed is False
    assert "outside canonical" in reason


def test_command_exit_zero_requires_real_exit_code():
    verifier = Verifier()
    assert verifier.verify({"type": "command_exit_zero"}, {"is_error": False})["ok"] is False
    assert verifier.verify({"type": "command_exit_zero"}, {"is_error": False, "exit_code": 0})["ok"] is True
    assert verifier.verify({"type": "command_exit_zero"}, {"is_error": True, "exit_code": 1})["ok"] is False


def test_window_verification_never_assumes_success_when_unavailable(monkeypatch):
    import builtins
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "pygetwindow":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    result = Verifier().verify({"type": "window_title_contains", "text": "Notepad"})
    assert result["ok"] is False
    assert result["verifiable"] is False


def test_mobile_approval_is_bound_to_challenged_connection():
    bridge = MobileBridge()
    event = threading.Event()
    bridge._pending_approvals["request-1"] = event
    bridge._approval_results["request-1"] = False
    bridge._approval_clients["request-1"] = "trusted-client"

    assert bridge.resolve_approval("request-1", True, client_id="other-client") is False
    assert event.is_set() is False
    assert bridge.resolve_approval("request-1", True, client_id="trusted-client") is True
    assert event.is_set() is True
    assert bridge._approval_results["request-1"] is True


def test_powershell_uses_process_exit_code(monkeypatch):
    class FakeProcess:
        returncode = 7
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, timeout=None):
            return "output", ""

    monkeypatch.setattr("tools.powershell_executor.subprocess.Popen", FakeProcess)
    result = PowerShellExecutorTool().execute("Write-Output test")
    assert result["is_error"] is True
    assert result["exit_code"] == 7


def test_powershell_timeout_is_reported_as_error(monkeypatch):
    class FakeProcess:
        returncode = -9
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, timeout=None):
            if timeout is not None:
                import subprocess
                raise subprocess.TimeoutExpired("powershell", timeout)
            return "", "terminated"
        def kill(self):
            pass

    monkeypatch.setattr("tools.powershell_executor.subprocess.Popen", FakeProcess)
    result = PowerShellExecutorTool().execute("Start-Sleep -Seconds 999")
    assert result["is_error"] is True
    assert result["timed_out"] is True


def test_atomic_json_write_replaces_complete_document(tmp_path):
    import json
    from agent.storage import atomic_write_json

    target = tmp_path / "state.json"
    atomic_write_json(target, {"version": 2, "items": [1, 2, 3]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2, "items": [1, 2, 3]}
    assert list(tmp_path.glob("*.tmp")) == []


def test_cloud_api_key_is_not_persisted(tmp_path, monkeypatch):
    import json
    import agent.llm_client as llm_module

    monkeypatch.setattr(llm_module, "FAILOVER_FILE", tmp_path / "llm_failover.json")
    client = llm_module.LLMClient()
    client.save_failover_cfg({"enabled": True, "cloud_api_key": "test-secret-value"})

    persisted = json.loads((tmp_path / "llm_failover.json").read_text(encoding="utf-8"))
    assert "cloud_api_key" not in persisted
    assert client.failover_cfg["cloud_api_key"] == "test-secret-value"


def test_all_shipped_plugins_have_valid_manifests_and_checksums():
    from agent.plugin_marketplace import verify_flat_plugin
    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    plugin_files = [p for p in plugins_dir.glob("*.py") if not p.name.startswith("__")]
    assert len(plugin_files) == 55
    for plugin in plugin_files:
        result = verify_flat_plugin(plugin)
        assert result.get("ok") is True, f"{plugin.name}: {result.get('errors')}"
        assert result.get("manifest", {}).get("tools"), f"{plugin.name}: no declared tools"


def test_tool_registry_rejects_duplicate_names():
    from agent.tool_registry import ToolRegistry
    from types import SimpleNamespace

    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools = {"existing": object()}
    registry._tool_permissions = {}
    with pytest.raises(ValueError, match="Duplicate tool name"):
        registry.register(SimpleNamespace(name="existing"))


def test_read_only_role_blocks_manifest_declared_mutating_plugin(tmp_path):
    engine = PolicyEngine(path=tmp_path / "policy.json")
    engine.data["role"] = "read_only"
    decision = engine.evaluate_tool_call("forjob_create_tracker", {}, permissions=["filesystem:write"])
    assert decision["allowed"] is False
    assert "read_only" in decision["reason"]
    launch = engine.evaluate_tool_call("open_app", {"target": "notepad"}, permissions=["process:execute"])
    assert launch["allowed"] is False


def test_undo_snapshot_failure_is_not_silently_ignored(tmp_path, monkeypatch):
    from agent.undo_manager import UndoManager
    import agent.undo_manager as undo_module

    existing = tmp_path / "existing.txt"
    existing.write_text("original", encoding="utf-8")
    manager = UndoManager(max_entries=2)
    monkeypatch.setattr(undo_module.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="Could not create undo snapshot"):
        manager.snapshot_before_write(str(existing))


def test_plugin_registry_loads_declared_core_workflows():
    from agent.tool_registry import ToolRegistry
    registry = ToolRegistry()
    required = {
        "browser_open", "web_search_open", "office_open", "office_new",
        "vscode_open", "run_learning_hour", "run_debug_logs",
        "run_competitor_scan", "forjob_create_tracker", "write_docx", "write_pdf",
    }
    missing = sorted(required - set(registry._tools))
    assert not missing, f"Expected shipped tools were not registered: {missing}"


def test_remote_mobile_server_requires_strong_token(monkeypatch):
    from mobile_api.server import start_server
    monkeypatch.delenv("VOICEASSISTANT_MOBILE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        start_server(host="0.0.0.0", port=0)


def test_mobile_websocket_rejects_wrong_token_before_accept(monkeypatch):
    import asyncio
    from mobile_api.server import websocket_endpoint

    monkeypatch.setenv("VOICEASSISTANT_MOBILE_TOKEN", "correct-token-value-that-is-long-enough-123456")

    class FakeWebSocket:
        headers = {"x-voiceassistant-token": "wrong-token"}
        closed = False
        async def close(self, code, reason):
            self.closed = True
            self.code = code
            self.reason = reason

    socket = FakeWebSocket()
    asyncio.run(websocket_endpoint(socket))
    assert socket.closed is True
    assert socket.code == 1008


def test_registry_exposes_manifest_permissions_to_policy():
    from agent.tool_registry import ToolRegistry
    registry = ToolRegistry()
    assert "filesystem:write" in registry.get_permissions("forjob_create_tracker")
    assert "filesystem:write" in registry.get_permissions("write_docx")


def test_mobile_approval_payload_requires_actual_boolean():
    from mobile_api.server import _validate_approval_response
    assert _validate_approval_response({"request_id": "abc", "approved": True}) == ("abc", True)
    with pytest.raises(ValueError, match="JSON boolean"):
        _validate_approval_response({"request_id": "abc", "approved": "false"})


def test_mobile_user_message_has_type_and_size_limits():
    from mobile_api.server import _validate_user_message
    assert _validate_user_message({"text": "  hello  "}) == "hello"
    with pytest.raises(ValueError, match="must be a string"):
        _validate_user_message({"text": ["hello"]})
    with pytest.raises(ValueError, match="10,000"):
        _validate_user_message({"text": "x" * 10001})


def test_corrupt_conversation_history_is_preserved_before_recovery(tmp_path):
    from agent.history_manager import HistoryManager
    manager = HistoryManager()
    manager.history_file = tmp_path / "conversation_history.json"
    manager.history_file.write_text("{not valid json", encoding="utf-8")

    assert manager.load_conversation() == []
    backups = list(tmp_path.glob("conversation_history.json.corrupt.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not valid json"


def test_desktop_deny_resolves_mobile_routed_approval_immediately():
    bridge = MobileBridge()
    event = threading.Event()
    bridge._pending_approvals["request-desktop-deny"] = event
    bridge._approval_results["request-desktop-deny"] = False
    bridge._approval_clients["request-desktop-deny"] = "mobile-client"
    bridge._active_approval_id = "request-desktop-deny"

    assert bridge.deny_desktop_approval() is True
    assert event.is_set() is True
    assert bridge._approval_results["request-desktop-deny"] is False


def test_plan_executor_never_auto_approves_mutating_manifest_tool():
    from agent.plan_executor import PlanExecutor

    class Registry:
        def get_permissions(self, name):
            return ["filesystem:write"] if name == "read_document" else ["filesystem:read"]

    executor = PlanExecutor(
        Registry(), lambda *a: None, lambda *a: None, lambda *a: None,
        lambda *a: None,
    )
    assert executor._can_auto_approve_read_tool("read_document", "low") is False
    assert executor._can_auto_approve_read_tool("read_document", "high") is False
    assert executor._can_auto_approve_read_tool("github_list_repos", "low") is True


def test_policy_allows_supported_desktop_relative_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / "Desktop").mkdir(parents=True)
    engine = PolicyEngine(path=tmp_path / "policy.json")
    engine.data["allowed_write_roots"] = ["Desktop"]
    allowed, reason = engine.evaluate_write_path("Desktop/ForJob/output.txt")
    assert allowed is True, reason


def test_undo_snapshot_resolves_desktop_relative_existing_file(tmp_path, monkeypatch):
    import agent.undo_manager as undo_module
    from agent.undo_manager import UndoManager

    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(undo_module, "UNDO_DIR", tmp_path / "undo")
    monkeypatch.setattr(undo_module, "UNDO_LOG", tmp_path / "undo_stack.json")
    target = desktop / "existing.txt"
    target.write_text("before", encoding="utf-8")

    manager = UndoManager()
    backup = manager.snapshot_before_write("Desktop/existing.txt")
    assert backup is not None
    assert Path(backup).read_text(encoding="utf-8") == "before"
    target.write_text("after", encoding="utf-8")
    manager.record_write("Desktop/existing.txt", backup, "after")
    result = manager.undo_last()
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "before"


def test_policy_fails_closed_when_allowed_roots_are_empty(tmp_path):
    engine = PolicyEngine(path=tmp_path / "policy.json")
    engine.data["allowed_write_roots"] = []
    allowed, reason = engine.evaluate_write_path(str(tmp_path / "output.txt"))
    assert allowed is False
    assert "No allowed_write_roots" in reason
