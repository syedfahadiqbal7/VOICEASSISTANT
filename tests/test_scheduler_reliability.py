"""Scheduler durability, crash recovery and retry regression tests."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

import pytest

import agent.scheduler as scheduler_module
from agent.scheduler import JobScheduler


def _scheduler(tmp_path, monkeypatch, handler=None):
    path = tmp_path / "scheduled_jobs.json"
    monkeypatch.setattr(scheduler_module, "SCHEDULE_FILE", path)
    return JobScheduler(on_job=handler), path


def test_running_job_is_requeued_after_process_restart(tmp_path, monkeypatch):
    scheduler, path = _scheduler(tmp_path, monkeypatch)
    path.write_text(json.dumps([{
        "id": "crashed-1", "prompt": "remind me", "run_at": datetime.now().isoformat(),
        "repeat": None, "enabled": True, "status": "running", "attempts": 1,
    }]), encoding="utf-8")
    restarted = JobScheduler()
    job = restarted.list_jobs()[0]
    assert job["status"] == "pending"
    assert job["enabled"] is True
    assert job["recovered_after_crash"] is True
    assert json.loads(path.read_text(encoding="utf-8"))[0]["status"] == "pending"


def test_one_time_job_is_completed_only_after_handler_success(tmp_path, monkeypatch):
    seen = []
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda job: seen.append(job["id"]) or True)
    job = scheduler.add_once("do task", datetime.now() - timedelta(seconds=1))
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert seen == [job["id"]]
    assert stored["status"] == "completed"
    assert stored["enabled"] is False


def test_failed_job_is_retried_and_eventually_marked_failed(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda _job: False)
    job = scheduler.add_once("flaky task", datetime.now() - timedelta(seconds=1))
    for expected_attempt in (1, 2, 3):
        scheduler._fire(job)
        stored = scheduler.list_jobs()[0]
        assert stored["attempts"] == expected_attempt
        if expected_attempt < 3:
            assert stored["status"] == "pending"
            stored["run_at"] = (datetime.now() - timedelta(seconds=1)).isoformat()
        else:
            assert stored["status"] == "failed"
            assert stored["enabled"] is False


def test_interval_job_reschedules_after_success(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda _job: True)
    job = scheduler.add_interval("repeat task", 10)
    original_run_at = job["run_at"]
    job["run_at"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    scheduler.jobs[0]["run_at"] = job["run_at"]
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert stored["status"] == "pending"
    assert stored["enabled"] is True
    assert stored["run_at"] != original_run_at
    assert stored["attempts"] == 0


def test_cancelled_job_cannot_fire(tmp_path, monkeypatch):
    calls = []
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda job: calls.append(job))
    job = scheduler.add_once("cancel me", datetime.now() - timedelta(seconds=1))
    assert scheduler.cancel(job["id"]) is True
    scheduler._fire(job)
    assert calls == []
    assert scheduler.list_jobs()[0]["status"] == "cancelled"


def test_empty_prompt_and_invalid_interval_are_rejected(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        scheduler.add_once("  ", datetime.now())
    with pytest.raises(ValueError):
        scheduler.add_interval("valid prompt", 0)


def test_corrupt_schedule_is_quarantined(tmp_path, monkeypatch):
    scheduler, path = _scheduler(tmp_path, monkeypatch)
    path.write_text("{broken json", encoding="utf-8")
    restarted = JobScheduler()
    assert restarted.list_jobs() == []
    assert list(tmp_path.glob("scheduled_jobs.json.corrupt.*.bak"))
    assert path.read_text(encoding="utf-8") == "{broken json"


def test_concurrent_fire_attempts_execute_handler_once(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def handler(job):
        calls.append(job["id"])
        entered.set()
        release.wait(timeout=2)
        return True
    scheduler, _ = _scheduler(tmp_path, monkeypatch, handler)
    job = scheduler.add_once("single execution", datetime.now() - timedelta(seconds=1))
    first = threading.Thread(target=scheduler._fire, args=(job,))
    first.start()
    assert entered.wait(timeout=1)
    scheduler._fire(job)
    release.set()
    first.join(timeout=2)
    assert calls == [job["id"]]
    assert scheduler.list_jobs()[0]["status"] == "completed"


def test_running_job_cannot_be_cancelled_mid_execution(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch)
    job = scheduler.add_once("in progress", datetime.now() - timedelta(seconds=1))
    scheduler.jobs[0]["status"] = "running"
    assert scheduler.cancel(job["id"]) is False
    assert scheduler.list_jobs()[0]["status"] == "running"


def test_completion_checkpoint_failure_requeues_job(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda _job: True)
    job = scheduler.add_once("checkpoint test", datetime.now() - timedelta(seconds=1))
    original_save = scheduler.save
    calls = {"count": 0}
    def fail_second_save():
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated disk failure")
        original_save()
    monkeypatch.setattr(scheduler, "save", fail_second_save)
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert stored["status"] == "pending"
    assert stored["enabled"] is True
    assert "checkpoint failed" in stored["last_error"].lower()
