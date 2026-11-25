from agent.scheduled_execution_ledger import (
    ScheduledExecutionLedger,
    ScheduledExecutionLedgerError,
)


def test_claim_is_persisted_before_tool_execution_and_success_is_idempotent(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ScheduledExecutionLedger(path)
    assert ledger.claim("exec-1", "open_app") == "claimed"
    stored = ScheduledExecutionLedger(path)
    assert stored.get("exec-1")["status"] == "started"

    ledger.complete("exec-1", success=True)
    reloaded = ScheduledExecutionLedger(path)
    assert reloaded.claim("exec-1", "open_app") == "succeeded"


def test_duplicate_claim_in_same_process_reports_in_progress(tmp_path):
    ledger = ScheduledExecutionLedger(tmp_path / "ledger.json")
    assert ledger.claim("exec-2", "write_developer_code") == "claimed"
    assert ledger.claim("exec-2", "write_developer_code") == "in_progress"


def test_started_execution_after_restart_becomes_uncertain_and_is_never_reclaimed(tmp_path):
    path = tmp_path / "ledger.json"
    first = ScheduledExecutionLedger(path)
    assert first.claim("exec-3", "open_app") == "claimed"

    # A second instance can see the live owner and must defer rather than replay.
    concurrent = ScheduledExecutionLedger(path)
    assert concurrent.claim("exec-3", "open_app") == "in_progress"

    # Simulate a previous process that no longer exists.
    import agent.scheduled_execution_ledger as ledger_module
    with ledger_module._ACTIVE_EXECUTIONS_LOCK:
        ledger_module._ACTIVE_EXECUTIONS.discard((str(path.resolve()), "exec-3"))
    first._entries["exec-3"]["owner_pid"] = -1
    first._entries["exec-3"]["owner_process_start"] = 0
    first._save()
    restarted = ScheduledExecutionLedger(path)
    assert restarted.claim("exec-3", "open_app") == "uncertain"
    assert restarted.claim("exec-3", "open_app") == "uncertain"


def test_uncertain_tool_outcome_is_not_reexecuted(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ScheduledExecutionLedger(path)
    assert ledger.claim("exec-4", "write_developer_code") == "claimed"
    ledger.complete("exec-4", success=False, detail="tool returned an error")
    assert ledger.claim("exec-4", "write_developer_code") == "uncertain"


def test_failed_persistence_does_not_grant_execution_claim(tmp_path, monkeypatch):
    import agent.scheduled_execution_ledger as module

    ledger = ScheduledExecutionLedger(tmp_path / "ledger.json")
    monkeypatch.setattr(module, "atomic_write_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    try:
        ledger.claim("exec-5", "open_app")
    except ScheduledExecutionLedgerError:
        pass
    else:
        raise AssertionError("claim must fail when its durable checkpoint fails")
    assert ledger.get("exec-5") is None


def test_ledger_rejects_unknown_format_and_fails_closed(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text('{"version":99,"entries":{}}', encoding="utf-8")
    try:
        ScheduledExecutionLedger(path)
    except ScheduledExecutionLedgerError:
        pass
    else:
        raise AssertionError("unknown ledger format must not be treated as empty")


def test_completion_checkpoint_failure_marks_execution_ambiguous(tmp_path, monkeypatch):
    import agent.scheduled_execution_ledger as module

    ledger = ScheduledExecutionLedger(tmp_path / "ledger.json")
    assert ledger.claim("exec-6", "open_app") == "claimed"
    monkeypatch.setattr(
        module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        ledger.complete("exec-6", success=True)
    except ScheduledExecutionLedgerError:
        pass
    else:
        raise AssertionError("completion checkpoint failure must be surfaced")

    monkeypatch.undo()
    assert ledger.claim("exec-6", "open_app") == "uncertain"


def test_ledger_capacity_fails_closed(tmp_path):
    ledger = ScheduledExecutionLedger(tmp_path / "ledger.json", max_entries=1)
    assert ledger.claim("exec-7", "open_app") == "claimed"
    ledger.complete("exec-7", success=True)
    try:
        ledger.claim("exec-8", "open_app")
    except ScheduledExecutionLedgerError as error:
        assert "full" in str(error).lower()
    else:
        raise AssertionError("full ledger must block untracked side effects")



def test_execution_ids_are_scoped_to_their_ledger_file(tmp_path):
    first = ScheduledExecutionLedger(tmp_path / "first.json")
    second = ScheduledExecutionLedger(tmp_path / "second.json")

    assert first.claim("shared-id", "open_app") == "claimed"
    assert second.claim("shared-id", "open_app") == "claimed"
    first.complete("shared-id", success=True)
    second.complete("shared-id", success=True)


def test_execution_id_cannot_be_reused_for_a_different_tool(tmp_path):
    ledger = ScheduledExecutionLedger(tmp_path / "ledger.json")
    assert ledger.claim("bound-id", "open_app") == "claimed"
    ledger.complete("bound-id", success=True)

    try:
        ledger.claim("bound-id", "delete_file")
    except ScheduledExecutionLedgerError as error:
        assert "different tool" in str(error).lower()
    else:
        raise AssertionError("an execution ID must remain bound to its original tool")


def test_ledger_rejects_entries_without_a_valid_tool_binding(tmp_path):
    from agent.storage import atomic_write_json

    path = tmp_path / "ledger.json"
    atomic_write_json(
        path,
        {"version": 1, "entries": {"broken": {"status": "succeeded"}}},
        encrypt=True,
    )
    try:
        ScheduledExecutionLedger(path)
    except ScheduledExecutionLedgerError as error:
        assert "invalid tool binding" in str(error).lower()
    else:
        raise AssertionError("malformed ledger entries must fail closed")
