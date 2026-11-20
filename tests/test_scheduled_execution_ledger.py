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
