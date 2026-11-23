"""Tests for encrypted, bounded scheduled execution history."""
from __future__ import annotations

import json

import pytest

from agent.scheduler_history import (
    SchedulerExecutionHistory,
    SchedulerHistoryError,
)


def _job(execution_id="exec-1"):
    return {
        "id": "job-1",
        "execution_id": execution_id,
        "label": "Daily report",
        "run_at": "2026-11-23T08:00:00+04:00",
        "prompt": "token=must-not-be-persisted",
    }


def test_history_records_one_occurrence_across_retries(tmp_path):
    path = tmp_path / "history.json"
    history = SchedulerExecutionHistory(path, max_records=10)

    history.record_started(_job())
    history.record_outcome("exec-1", "retrying", "retry scheduled")
    history.record_started(_job())
    history.record_outcome("exec-1", "succeeded")

    records = history.list_records()
    assert len(records) == 1
    assert records[0]["execution_id"] == "exec-1"
    assert records[0]["status"] == "succeeded"
    assert records[0]["attempts"] == 2
    assert records[0]["started_at"]
    assert records[0]["finished_at"]


def test_history_is_encrypted_and_excludes_prompt_and_raw_diagnostic(tmp_path):
    path = tmp_path / "history.json"
    history = SchedulerExecutionHistory(path)
    history.record_started(_job())
    history.record_outcome("exec-1", "failed", "safe category")

    raw = path.read_bytes()
    assert b"must-not-be-persisted" not in raw
    assert b"token=" not in raw
    assert b"Daily report" not in raw


def test_history_rejects_terminal_replay_or_rewrite(tmp_path):
    history = SchedulerExecutionHistory(tmp_path / "history.json")
    history.record_started(_job())
    history.record_outcome("exec-1", "succeeded")
    with pytest.raises(SchedulerHistoryError):
        history.record_started(_job())
    with pytest.raises(SchedulerHistoryError):
        history.record_outcome("exec-1", "failed")


def test_history_requires_started_occurrence(tmp_path):
    history = SchedulerExecutionHistory(tmp_path / "history.json")
    with pytest.raises(SchedulerHistoryError):
        history.record_outcome("missing", "succeeded")


def test_history_bounds_retention_and_returns_newest_first(tmp_path):
    history = SchedulerExecutionHistory(tmp_path / "history.json", max_records=2)
    for index in range(3):
        execution_id = f"exec-{index}"
        history.record_started(_job(execution_id))
        history.record_outcome(execution_id, "succeeded")

    records = history.list_records()
    assert [record["execution_id"] for record in records] == ["exec-2", "exec-1"]
    assert len(history.list_records(limit=1000)) == 2


def test_history_fails_closed_on_malformed_document(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"version": 999, "records": []}), encoding="utf-8")
    with pytest.raises(SchedulerHistoryError):
        SchedulerExecutionHistory(path)


def test_history_rejects_missing_stable_identity(tmp_path):
    history = SchedulerExecutionHistory(tmp_path / "history.json")
    with pytest.raises(SchedulerHistoryError):
        history.record_started({"id": "job-1", "run_at": "2026-11-23T08:00:00"})
