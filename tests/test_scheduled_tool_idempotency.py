from agent.conversation_manager import ConversationManager
from agent.scheduled_execution_ledger import ScheduledExecutionLedger
from agent.scheduler import JobExecutionResult


class _Policy:
    role = "operator"

    def evaluate_tool_call(self, *_args, **_kwargs):
        return {"allowed": True}

    def is_trusted_scheduled_open(self, _target):
        return True


class _Tools:
    def __init__(self):
        self.calls = []

    def get_permissions(self, _name):
        return []

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        return {"content": "opened", "is_error": False}


def _manager(tmp_path, *, gate=None):
    manager = object.__new__(ConversationManager)
    manager.scheduled_execution_ledger = ScheduledExecutionLedger(
        tmp_path / "scheduled_execution_ledger.json"
    )
    manager.policy = _Policy()
    manager.tool_registry = _Tools()
    manager.undo = None
    manager.on_status = lambda *_args: None
    manager.on_speak = lambda *_args: None
    manager.on_security_gate = gate or (lambda _display, callback, _risk: callback())
    manager._log = lambda *_args, **_kwargs: None
    return manager


def test_scheduled_tool_replay_after_success_skips_second_side_effect(tmp_path):
    manager = _manager(tmp_path)
    first = manager._run_tool_direct(
        "open_app", {"target": "notepad"}, "Open notepad",
        auto_approve=True, execution_id="same-occurrence",
    )
    second = manager._run_tool_direct(
        "open_app", {"target": "notepad"}, "Open notepad",
        auto_approve=True, execution_id="same-occurrence",
    )

    assert first is True
    assert second is True
    assert manager.tool_registry.calls == [("open_app", {"target": "notepad"})]


def test_ambiguous_execution_from_previous_process_fails_closed(tmp_path):
    path = tmp_path / "scheduled_execution_ledger.json"
    previous_process = ScheduledExecutionLedger(path)
    assert previous_process.claim("crash-window", "open_app") == "claimed"
    import agent.scheduled_execution_ledger as ledger_module
    with ledger_module._ACTIVE_EXECUTIONS_LOCK:
        ledger_module._ACTIVE_EXECUTIONS.discard("crash-window")
    previous_process._entries["crash-window"]["owner_pid"] = -1
    previous_process._entries["crash-window"]["owner_process_start"] = 0
    previous_process._save()

    manager = _manager(tmp_path)
    outcome = manager._run_tool_direct(
        "open_app", {"target": "notepad"}, "Open notepad",
        auto_approve=True, execution_id="crash-window",
    )

    assert isinstance(outcome, JobExecutionResult)
    assert outcome.success is False
    assert outcome.retryable is False
    assert "Automatic replay is blocked" in outcome.message
    assert manager.tool_registry.calls == []


def test_duplicate_approval_callback_invokes_tool_only_once(tmp_path):
    def approve_twice(_display, callback, _risk):
        callback()
        callback()

    manager = _manager(tmp_path, gate=approve_twice)
    outcome = manager._run_tool_direct(
        "open_app", {"target": "notepad"}, "Open notepad",
        auto_approve=False, execution_id="approval-twice",
    )

    assert outcome is True
    assert manager.tool_registry.calls == [("open_app", {"target": "notepad"})]


def test_missing_execution_id_blocks_scheduled_side_effect(tmp_path):
    manager = _manager(tmp_path)
    outcome = manager._run_tool_direct(
        "open_app", {"target": "notepad"}, "Open notepad",
        auto_approve=True, execution_id=None,
    )
    assert isinstance(outcome, JobExecutionResult)
    assert outcome.retryable is False
    assert manager.tool_registry.calls == []


def test_failed_fast_path_is_not_reinterpreted_as_a_successful_reminder(tmp_path):
    manager = _manager(tmp_path)
    spoken = []
    manager.on_speak = lambda message: spoken.append(message)
    manager._execute_scheduled_prompt_fast = lambda _prompt, _execution_id: False

    outcome = manager._on_scheduled_job({
        "id": "job-1",
        "execution_id": "occurrence-1",
        "prompt": "open notepad",
    })

    assert outcome is False
    assert spoken == []


def test_approval_callback_arriving_after_timeout_cannot_execute(tmp_path, monkeypatch):
    import agent.conversation_manager as manager_module

    callback_holder = {}

    class TimedOutQueue:
        def put(self, _value):
            pass

        def get(self, timeout=None):
            assert timeout == 300
            raise manager_module.queue.Empty()

    monkeypatch.setattr(manager_module.queue, "Queue", lambda: TimedOutQueue())
    manager = _manager(
        tmp_path,
        gate=lambda _display, callback, _risk: callback_holder.setdefault("callback", callback),
    )

    outcome = manager._run_tool_direct(
        "open_app", {"target": "notepad"}, "Open notepad",
        auto_approve=False, execution_id="late-approval",
    )
    assert outcome is False

    callback_holder["callback"]()
    assert manager.tool_registry.calls == []


def test_open_app_fast_path_matches_and_propagates_execution_id(tmp_path):
    manager = _manager(tmp_path)
    captured = {}
    manager._is_trusted_scheduled_open = lambda _target: True

    def run_tool(name, args, display, auto_approve=False, execution_id=None):
        captured.update({
            "name": name,
            "args": args,
            "display": display,
            "auto_approve": auto_approve,
            "execution_id": execution_id,
        })
        return True

    manager._run_tool_direct = run_tool
    assert manager._execute_scheduled_prompt_fast("Open notepad", "occurrence-42") is True
    assert captured == {
        "name": "open_app",
        "args": {"target": "notepad"},
        "display": "Scheduled: Open notepad",
        "auto_approve": True,
        "execution_id": "occurrence-42",
    }
