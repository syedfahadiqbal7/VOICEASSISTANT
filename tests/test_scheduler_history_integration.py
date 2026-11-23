"""Scheduler-to-history integration and fail-closed behavior."""
from __future__ import annotations

from datetime import datetime, timedelta

import agent.scheduler as scheduler_module
import agent.scheduler_history as history_module
from agent.scheduler import JobScheduler


def _scheduler(tmp_path, monkeypatch, handler):
    monkeypatch.setattr(scheduler_module, "SCHEDULE_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(history_module, "HISTORY_FILE", tmp_path / "execution-history.json")
    return JobScheduler(on_job=handler)


def test_scheduler_persists_successful_occurrence_history(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch, lambda _job: True)
    job = scheduler.add_once("private prompt should not be stored", datetime.now() - timedelta(seconds=1))

    scheduler._fire(job)

    records = scheduler.history.list_records()
    assert len(records) == 1
    assert records[0]["execution_id"] == job["execution_id"]
    assert records[0]["status"] == "succeeded"
    assert records[0]["attempts"] == 1


def test_scheduler_retries_update_same_history_occurrence(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch, lambda _job: False)
    job = scheduler.add_once("retry safely", datetime.now() - timedelta(seconds=1))

    scheduler._fire(job)
    scheduler.jobs[0]["run_at"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    scheduler._fire(job)

    records = scheduler.history.list_records()
    assert len(records) == 1
    assert records[0]["execution_id"] == job["execution_id"]
    assert records[0]["status"] == "retrying"
    assert records[0]["attempts"] == 2


def test_scheduler_does_not_invoke_handler_when_history_is_unavailable(tmp_path, monkeypatch):
    calls = []
    scheduler = _scheduler(tmp_path, monkeypatch, lambda job: calls.append(job) or True)
    scheduler.history = None
    scheduler.history_error = "simulated storage failure"
    job = scheduler.add_once("must not execute", datetime.now() - timedelta(seconds=1))

    scheduler._fire(job)

    stored = scheduler.list_jobs()[0]
    assert calls == []
    assert stored["status"] == "failed"
    assert stored["enabled"] is False
    assert "history unavailable" in stored["last_error"].lower()
