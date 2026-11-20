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
