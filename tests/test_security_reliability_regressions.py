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
    monkeypatch.setattr(undo_module, "atomic_write_encrypted_bytes", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
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
    assert undo_module.load_encrypted_bytes(backup) == b"before"
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


def test_mobile_rate_limiter_applies_global_and_user_message_quotas():
    from mobile_api.server import SlidingWindowRateLimiter

    limiter = SlidingWindowRateLimiter(window_seconds=60, max_messages=3, max_user_messages=1)
    assert limiter.allow("user_message", now=0.0) is True
    assert limiter.allow("user_message", now=1.0) is False
    assert limiter.allow("ping", now=2.0) is True
    assert limiter.allow("get_session_state", now=3.0) is True
    assert limiter.allow("ping", now=4.0) is False
    assert limiter.allow("user_message", now=61.0) is True


def test_mobile_schedule_cancel_requires_bounded_string_id():
    from mobile_api.server import _validate_job_id

    assert _validate_job_id({"job_id": "job-123"}) == "job-123"
    assert _validate_job_id({"id": "job-456"}) == "job-456"
    for payload in ({"job_id": {"id": "x"}}, {"job_id": ""}, {"job_id": "x" * 129}):
        with pytest.raises(ValueError, match="job_id"):
            _validate_job_id(payload)


def test_mobile_tls_configuration_requires_a_matching_certificate_and_key(tmp_path, monkeypatch):
    from mobile_api.server import _tls_options_from_env

    monkeypatch.delenv("VOICEASSISTANT_TLS_CERTFILE", raising=False)
    monkeypatch.delenv("VOICEASSISTANT_TLS_KEYFILE", raising=False)
    assert _tls_options_from_env() == {}

    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    cert.write_text("test certificate", encoding="utf-8")
    key.write_text("test private key", encoding="utf-8")
    monkeypatch.setenv("VOICEASSISTANT_TLS_CERTFILE", str(cert))
    monkeypatch.delenv("VOICEASSISTANT_TLS_KEYFILE", raising=False)
    with pytest.raises(RuntimeError, match="both"):
        _tls_options_from_env()

    monkeypatch.setenv("VOICEASSISTANT_TLS_KEYFILE", str(key))
    assert _tls_options_from_env() == {
        "ssl_certfile": str(cert),
        "ssl_keyfile": str(key),
    }


def test_mobile_approval_disconnect_is_denied_when_desktop_fallback_is_absent():
    bridge = MobileBridge()
    event = threading.Event()
    bridge._pending_approvals["disconnect-request"] = event
    bridge._approval_results["disconnect-request"] = False
    bridge._approval_clients["disconnect-request"] = "mobile-123"

    bridge.client_disconnected("mobile-123")

    assert event.is_set() is True
    assert bridge._approval_results["disconnect-request"] is False


def test_connection_manager_disconnect_callback_and_snapshot_access():
    import asyncio
    from mobile_api.connection_manager import ConnectionManager

    class FakeWebSocket:
        async def accept(self):
            return None

    manager = ConnectionManager()
    disconnected = []
    manager.on_disconnect = disconnected.append
    socket = FakeWebSocket()
    connection_id = asyncio.run(manager.connect(socket))

    assert manager.connection_count() == 1
    first, first_id = manager.first_connection()
    assert first is socket
    assert first_id == connection_id

    manager.disconnect(socket)
    assert manager.connection_count() == 0
    assert disconnected == [connection_id]



def test_mobile_approval_handler_acknowledges_accepted_and_stale_requests(monkeypatch):
    import asyncio
    from mobile_api import server

    sent = []
    async def fake_send_personal(websocket, payload):
        sent.append(payload)

    monkeypatch.setattr(server.manager, "send_personal", fake_send_personal)
    monkeypatch.setattr(
        server.bridge,
        "resolve_approval",
        lambda request_id, approved, client_id: request_id == "active" and client_id == "phone-1",
    )

    class FakeWebSocket:
        pass

    socket = FakeWebSocket()
    assert asyncio.run(server._resolve_mobile_approval(socket, "active", True, "phone-1")) is True
    assert asyncio.run(server._resolve_mobile_approval(socket, "stale", True, "phone-1")) is False
    assert sent == [
        {"type": "approval_ack", "request_id": "active", "accepted": True},
        {"type": "approval_ack", "request_id": "stale", "accepted": False},
    ]



def test_mobile_platform_permissions_and_secure_storage_backup_policy():
    import plistlib
    import xml.etree.ElementTree as ET

    root = Path(__file__).resolve().parents[1]
    android_manifest = ET.parse(
        root / "voice_assistant_mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).getroot()
    android_ns = "{http://schemas.android.com/apk/res/android}"
    application = android_manifest.find("application")
    assert application is not None
    assert application.attrib.get(android_ns + "allowBackup") == "false"
    assert application.attrib.get(android_ns + "usesCleartextTraffic") == "true"
    permissions = {
        element.attrib.get(android_ns + "name")
        for element in android_manifest.findall("uses-permission")
    }
    assert "android.permission.RECORD_AUDIO" in permissions
    assert "android.permission.INTERNET" in permissions

    with (root / "voice_assistant_mobile" / "ios" / "Runner" / "Info.plist").open("rb") as stream:
        ios_plist = plistlib.load(stream)
    assert ios_plist.get("NSMicrophoneUsageDescription")
    assert ios_plist.get("NSSpeechRecognitionUsageDescription")
    assert ios_plist.get("NSLocalNetworkUsageDescription")



def test_desktop_approval_resolution_acknowledges_mobile_prompt(monkeypatch):
    import importlib

    bridge_module = importlib.import_module("mobile_api.bridge")
    sent = []
    async def fake_broadcast(payload):
        sent.append(payload)

    monkeypatch.setattr(bridge_module.manager, "broadcast", fake_broadcast)
    instance = MobileBridge()
    event = threading.Event()
    instance._pending_approvals["desktop-request"] = event
    instance._approval_results["desktop-request"] = False
    instance._approval_clients["desktop-request"] = "phone-1"

    assert instance.resolve_approval("desktop-request", False) is True
    assert event.is_set() is True
    assert sent == [{
        "type": "approval_ack",
        "request_id": "desktop-request",
        "accepted": True,
    }]



def test_desktop_ui_dispatcher_runs_worker_callbacks_only_on_main_loop():
    import queue
    import threading
    from ui.app_window import AgenticVoiceAssistant

    class FakeUI:
        _post_ui = AgenticVoiceAssistant._post_ui
        _drain_ui_queue = AgenticVoiceAssistant._drain_ui_queue

        def __init__(self):
            self._closing = False
            self._ui_queue = queue.Queue()
            self._ui_poll_id = None
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))
            return f"poll-{len(self.scheduled)}"

    ui = FakeUI()
    observed_threads = []
    main_thread = threading.get_ident()

    worker = threading.Thread(
        target=lambda: ui._post_ui(lambda: observed_threads.append(threading.get_ident()))
    )
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert observed_threads == []
    ui._drain_ui_queue()
    assert observed_threads == [main_thread]
    assert ui._ui_poll_id == "poll-1"
    assert ui.scheduled[0][0] == 25


def test_desktop_ui_dispatcher_drops_callbacks_after_shutdown():
    import queue
    from ui.app_window import AgenticVoiceAssistant

    class FakeUI:
        _post_ui = AgenticVoiceAssistant._post_ui

        def __init__(self):
            self._closing = True
            self._ui_queue = queue.Queue()

    ui = FakeUI()
    assert ui._post_ui(lambda: None) is False
    assert ui._ui_queue.empty()



def test_scheduler_form_rejects_invalid_minutes_and_path_traversal():
    from ui.scheduler_viewer import SchedulerViewer

    class Field:
        def __init__(self, value):
            self.value = value

        def get(self, *args):
            return self.value

    class FakeSchedulerForm:
        _build_prompt = SchedulerViewer._build_prompt

        def __init__(self, minutes="2", name="../outside", action="Create a text file on Desktop"):
            self.action_var = Field(action)
            self.minutes_entry = Field(minutes)
            self.file_entry = Field(name)
            self.content_entry = Field("safe content")
            self.custom_entry = Field("")

    with pytest.raises(ValueError, match="whole number"):
        FakeSchedulerForm(minutes="abc")._build_prompt()
    with pytest.raises(ValueError, match="between 1 and 525600"):
        FakeSchedulerForm(minutes="0")._build_prompt()
    with pytest.raises(ValueError, match="file name only"):
        FakeSchedulerForm(name="../outside")._build_prompt()


def test_scheduler_form_normalizes_text_extension_and_limits_custom_app():
    from ui.scheduler_viewer import SchedulerViewer

    class Field:
        def __init__(self, value):
            self.value = value

        def get(self, *args):
            return self.value

    class FakeSchedulerForm:
        _build_prompt = SchedulerViewer._build_prompt

        def __init__(self, action, app="notepad", name="notes", content="hello"):
            self.action_var = Field(action)
            self.app_var = Field(app)
            self.minutes_entry = Field("2")
            self.when_var = Field("Once, after a delay")
            self.file_entry = Field(name)
            self.content_entry = Field(content)
            self.custom_entry = Field("")

    prompt = FakeSchedulerForm("Create a text file on Desktop")._build_prompt()
    assert "notes.txt" in prompt
    assert "hello" in prompt
    with pytest.raises(ValueError, match="approved list"):
        FakeSchedulerForm("Open an app", app="powershell.exe")._build_prompt()



def test_restart_spawns_replacement_before_scheduling_orderly_shutdown(monkeypatch):
    from agent import app_restart

    spawned = []
    monkeypatch.setattr(
        app_restart.subprocess,
        "Popen",
        lambda args, **kwargs: spawned.append((args, kwargs)),
    )

    class FakeRoot:
        def __init__(self):
            self.scheduled = None
            self.closed = False

        def after(self, delay, callback):
            self.scheduled = (delay, callback)

        def _on_close(self):
            self.closed = True

    root = FakeRoot()
    assert app_restart.restart_app(root) is True
    assert len(spawned) == 1
    assert root.scheduled is not None
    assert root.scheduled[0] == 0
    assert root.closed is False

    root.scheduled[1]()
    assert root.closed is True



def test_mobile_connection_limit_is_bounded_and_configurable(monkeypatch):
    from mobile_api.server import _max_mobile_connections

    monkeypatch.delenv("VOICEASSISTANT_MOBILE_MAX_CONNECTIONS", raising=False)
    assert _max_mobile_connections() == 5
    monkeypatch.setenv("VOICEASSISTANT_MOBILE_MAX_CONNECTIONS", "12")
    assert _max_mobile_connections() == 12
    for invalid in ("0", "33", "many"):
        monkeypatch.setenv("VOICEASSISTANT_MOBILE_MAX_CONNECTIONS", invalid)
        with pytest.raises(RuntimeError):
            _max_mobile_connections()


def test_connection_manager_rejects_excess_connections_without_accepting_socket():
    import asyncio
    from mobile_api.connection_manager import ConnectionManager

    class FakeWebSocket:
        def __init__(self):
            self.accepted = False

        async def accept(self):
            self.accepted = True

    manager = ConnectionManager()
    first = FakeWebSocket()
    second = FakeWebSocket()
    first_id = asyncio.run(manager.connect(first, max_connections=1))
    second_id = asyncio.run(manager.connect(second, max_connections=1))

    assert first_id
    assert second_id is None
    assert first.accepted is True
    assert second.accepted is False
    assert manager.connection_count() == 1



def test_mobile_api_stop_server_requests_shutdown_on_server_loop(monkeypatch):
    import threading
    from mobile_api import server

    monkeypatch.setattr(server, "_server_stop_requested", threading.Event())

    class FakeLoop:
        closed = False

        def is_closed(self):
            return self.closed

        def call_soon_threadsafe(self, callback, *args):
            callback(*args)

    class FakeServer:
        should_exit = False

    fake_loop = FakeLoop()
    fake_server = FakeServer()
    monkeypatch.setattr(server, "_active_server", fake_server)
    monkeypatch.setattr(server, "_active_server_loop", fake_loop)

    assert server.stop_server() is True
    assert fake_server.should_exit is True


def test_mobile_api_stop_server_is_safe_when_not_running(monkeypatch):
    import threading
    from mobile_api import server

    monkeypatch.setattr(server, "_server_stop_requested", threading.Event())
    monkeypatch.setattr(server, "_active_server", None)
    monkeypatch.setattr(server, "_active_server_loop", None)
    assert server.stop_server() is False



def test_mobile_api_startup_is_cancelled_if_shutdown_wins_race(monkeypatch):
    import threading
    from mobile_api import server

    requested = threading.Event()
    requested.set()
    monkeypatch.setattr(server, "_server_stop_requested", requested)
    monkeypatch.setattr(server, "_active_server", None)
    monkeypatch.setattr(server, "_active_server_loop", None)

    assert server.start_server() is None
    assert server._active_server is None



def test_connection_manager_capacity_reservation_is_atomic_under_concurrent_handshakes():
    import asyncio
    from mobile_api.connection_manager import ConnectionManager

    async def exercise():
        manager = ConnectionManager()
        release_handshakes = asyncio.Event()

        class FakeWebSocket:
            def __init__(self):
                self.accepted = False

            async def accept(self):
                self.accepted = True
                await release_handshakes.wait()

        sockets = [FakeWebSocket() for _ in range(3)]
        tasks = [
            asyncio.create_task(manager.connect(socket, max_connections=2))
            for socket in sockets
        ]
        await asyncio.sleep(0)
        assert manager._pending_connections == 2
        release_handshakes.set()
        results = await asyncio.gather(*tasks)
        return manager, sockets, results

    manager, sockets, results = asyncio.run(exercise())
    assert sum(result is not None for result in results) == 2
    assert sum(socket.accepted for socket in sockets) == 2
    assert manager.connection_count() == 2
