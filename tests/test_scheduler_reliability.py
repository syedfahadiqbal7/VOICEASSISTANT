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
    from agent.storage import load_json
    assert load_json(path, encrypt=True)[0]["status"] == "pending"


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



def test_execution_id_is_persisted_before_handler_and_exposed_to_callback(tmp_path, monkeypatch):
    observed = []
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda job: observed.append(job["execution_id"]) or True)
    job = scheduler.add_once("idempotent task", datetime.now() - timedelta(seconds=1))
    execution_id = job["execution_id"]
    scheduler._fire(job)
    assert observed == [execution_id]
    assert scheduler.list_jobs()[0]["execution_id"] == execution_id


def test_retry_reuses_same_execution_id(tmp_path, monkeypatch):
    observed = []
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda job: observed.append(job["execution_id"]) or False)
    job = scheduler.add_once("retry task", datetime.now() - timedelta(seconds=1))
    first_id = job["execution_id"]
    scheduler._fire(job)
    scheduler.jobs[0]["run_at"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    scheduler._fire(job)
    assert observed == [first_id, first_id]
    assert scheduler.list_jobs()[0]["execution_id"] == first_id


def test_crash_recovery_preserves_running_execution_id(tmp_path, monkeypatch):
    scheduler, path = _scheduler(tmp_path, monkeypatch)
    job = scheduler.add_once("crash during handler", datetime.now() - timedelta(seconds=1))
    execution_id = job["execution_id"]
    scheduler.jobs[0]["status"] = "running"
    scheduler.save()
    restarted = JobScheduler()
    recovered = restarted.list_jobs()[0]
    assert recovered["status"] == "pending"
    assert recovered["execution_id"] == execution_id


def test_interval_success_rotates_execution_id_for_next_occurrence(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda _job: True)
    job = scheduler.add_interval("interval id task", 10)
    first_id = job["execution_id"]
    scheduler.jobs[0]["run_at"] = (datetime.now() - timedelta(seconds=1)).isoformat()
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert stored["execution_id"] != first_id
    assert stored["status"] == "pending"



def test_interval_completion_checkpoint_failure_keeps_current_execution_id(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch, lambda _job: True)
    job = scheduler.add_interval("interval checkpoint task", 10)
    execution_id = job["execution_id"]
    original_save = scheduler.save
    calls = {"count": 0}
    def fail_completion_save():
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated completion write failure")
        original_save()
    monkeypatch.setattr(scheduler, "save", fail_completion_save)
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert stored["status"] == "pending"
    assert stored["execution_id"] == execution_id



def test_stop_reports_when_handler_exceeds_shutdown_grace_period(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def handler(job):
        calls.append(job["id"])
        entered.set()
        release.wait(timeout=3)
        return True
    scheduler, _ = _scheduler(tmp_path, monkeypatch, handler)
    job = scheduler.add_once("slow scheduled handler", datetime.now() - timedelta(seconds=1))
    scheduler.start()
    assert entered.wait(timeout=1)
    assert scheduler.stop(timeout=0.01) is False
    # A second start must not create a competing worker while the old handler runs.
    scheduler.start()
    assert calls == [job["id"]]
    release.set()
    worker = scheduler._thread
    assert worker is not None
    worker.join(timeout=1)
    assert not worker.is_alive()
    # Once the old worker has exited, the scheduler can be started and stopped again.
    scheduler.start()
    assert scheduler.stop(timeout=1) is True


def test_stop_without_started_worker_returns_true(tmp_path, monkeypatch):
    scheduler, _ = _scheduler(tmp_path, monkeypatch)
    assert scheduler.stop() is True


def test_deferred_execution_does_not_consume_retry_budget_or_rotate_id(tmp_path, monkeypatch):
    from agent.scheduler import JobExecutionResult

    scheduler, _ = _scheduler(
        tmp_path, monkeypatch,
        lambda _job: JobExecutionResult(
            success=False, retryable=True, defer=True, message="still running"
        ),
    )
    job = scheduler.add_once("wait for existing action", datetime.now() - timedelta(seconds=1))
    execution_id = job["execution_id"]
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert stored["status"] == "pending"
    assert stored["enabled"] is True
    assert stored["attempts"] == 0
    assert stored["execution_id"] == execution_id
    assert stored["execution_waiting"] is True
    assert stored["last_error"] == "still running"


def test_non_retryable_execution_failure_is_terminal(tmp_path, monkeypatch):
    from agent.scheduler import JobExecutionResult

    scheduler, _ = _scheduler(
        tmp_path, monkeypatch,
        lambda _job: JobExecutionResult(
            success=False, retryable=False, message="outcome uncertain; manual review required"
        ),
    )
    job = scheduler.add_once("do not replay side effect", datetime.now() - timedelta(seconds=1))
    scheduler._fire(job)
    stored = scheduler.list_jobs()[0]
    assert stored["status"] == "failed"
    assert stored["enabled"] is False
    assert stored["attempts"] == 1
    assert stored["last_error"] == "outcome uncertain; manual review required"
    assert stored["execution_waiting"] is False


def test_deferred_active_execution_cannot_be_cancelled(tmp_path, monkeypatch):
    from agent.scheduler import JobExecutionResult

    scheduler, _ = _scheduler(
        tmp_path, monkeypatch,
        lambda _job: JobExecutionResult(
            success=False, retryable=True, defer=True, message="still running"
        ),
    )
    job = scheduler.add_once("active callback", datetime.now() - timedelta(seconds=1))
    scheduler._fire(job)
    assert scheduler.list_jobs()[0]["execution_waiting"] is True
    assert scheduler.cancel(job["id"]) is False
    assert scheduler.list_jobs()[0]["status"] == "pending"
    assert scheduler.list_jobs()[0]["execution_waiting"] is True



def test_offset_aware_run_at_is_compared_as_an_absolute_instant():
    from datetime import timezone

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert JobScheduler._is_due(
        {"run_at": "2026-01-01T11:59:00+00:00"}, now
    )
    assert not JobScheduler._is_due(
        {"run_at": "2026-01-01T12:01:00+00:00"}, now
    )


def test_naive_legacy_run_at_can_be_compared_with_aware_now():
    from datetime import timezone

    run_at = (datetime.now() - timedelta(minutes=1)).isoformat()
    assert JobScheduler._is_due({"run_at": run_at}, datetime.now(timezone.utc))


def test_dst_fallback_offsets_represent_distinct_instants():
    from datetime import timezone

    first_occurrence = {"run_at": "2026-11-01T01:30:00-04:00"}
    second_occurrence = {"run_at": "2026-11-01T01:30:00-05:00"}
    between_occurrences = datetime(2026, 11, 1, 5, 45, tzinfo=timezone.utc)
    assert JobScheduler._is_due(first_occurrence, between_occurrences)
    assert not JobScheduler._is_due(second_occurrence, between_occurrences)


def test_invalid_persisted_run_at_is_retained_but_disabled(tmp_path, monkeypatch):
    scheduler, path = _scheduler(tmp_path, monkeypatch)
    path.write_text(json.dumps([{
        "id": "bad-time", "prompt": "do not lose me", "run_at": "not-a-date",
        "repeat": None, "enabled": True, "status": "pending",
    }]), encoding="utf-8")

    restarted = JobScheduler()
    job = restarted.list_jobs()[0]
    assert job["id"] == "bad-time"
    assert job["status"] == "failed"
    assert job["enabled"] is False
    assert "invalid persisted run_at" in job["last_error"].lower()


def test_malformed_persisted_entry_is_quarantined_not_silently_dropped(tmp_path, monkeypatch):
    scheduler, path = _scheduler(tmp_path, monkeypatch)
    path.write_text(json.dumps([
        {"id": "valid", "prompt": "keep", "run_at": datetime.now().isoformat(),
         "repeat": None, "enabled": True, "status": "pending"},
        {"prompt": "missing id", "run_at": datetime.now().isoformat()},
    ]), encoding="utf-8")

    restarted = JobScheduler()
    assert restarted.list_jobs() == []
    backups = list(tmp_path.glob("scheduled_jobs.json.corrupt.*.bak"))
    assert backups
    assert json.loads(backups[0].read_text(encoding="utf-8"))[0]["id"] == "valid"
