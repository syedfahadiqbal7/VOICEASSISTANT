# agent/scheduler.py
"""Durable in-process scheduler with crash recovery and bounded retries.

Execution is at-least-once: a crash after an external side effect but before its
completion checkpoint can replay the handler after restart. Side-effecting
handlers should therefore be idempotent where possible.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from config import BASE_DIR
from agent.storage import atomic_write_json, load_json
from agent.data_protection import DataProtectionError
from agent.calendar_recurrence import validate_calendar_recurrence, next_calendar_occurrence
from agent.scheduler_history import SchedulerExecutionHistory, SchedulerHistoryError

SCHEDULE_FILE = BASE_DIR / "history" / "scheduled_jobs.json"
MAX_ATTEMPTS = 3
RETRY_DELAYS_MINUTES = (1, 5, 15)
MAX_INTERVAL_MINUTES = 525600


def _validated_interval(value: Any) -> int:
    """Validate persisted/API interval metadata without lossy coercion."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Interval must be an integer number of minutes")
    if not 1 <= value <= MAX_INTERVAL_MINUTES:
        raise ValueError(f"Interval must be between 1 and {MAX_INTERVAL_MINUTES} minutes")
    return value

logger = logging.getLogger("voiceassistant.scheduler")


@dataclass(frozen=True)
class JobExecutionResult:
    """Explicit handler result for safe retry, deferral or terminal failure.

    Existing bool/None callbacks remain backward-compatible. A deferred result
    means the same logical execution is still active elsewhere in this process;
    it is rescheduled without consuming a retry attempt or rotating execution_id.
    """

    success: bool
    retryable: bool = True
    defer: bool = False
    message: Optional[str] = None

    def __post_init__(self):
        if self.defer and (self.success or not self.retryable):
            raise ValueError("A deferred execution must be unsuccessful and retryable")


def _now() -> datetime:
    # Keep legacy local-wall-clock storage for compatibility with existing jobs.
    return datetime.now()


def _run_at_timestamp(value: Any) -> float:
    """Convert an ISO-8601 run_at value to an absolute timestamp.

    Offset-aware values represent an explicit instant. Legacy naive values are
    interpreted in the host's local timezone, matching datetime.timestamp()
    semantics and the historical scheduler behavior. Comparing timestamps avoids
    TypeError when an aware value is compared with the legacy naive _now().
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_at must be a non-empty ISO-8601 string")
    parsed = datetime.fromisoformat(value.strip())
    return parsed.timestamp()


class JobScheduler:
    def __init__(self, on_job: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self.on_job = on_job
        self.jobs: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.history: Optional[SchedulerExecutionHistory] = None
        self.history_error: Optional[str] = None
        try:
            self.history = SchedulerExecutionHistory()
        except Exception as error:
            # Keep the desktop usable, but fail scheduled execution closed.
            self.history_error = type(error).__name__
            logger.exception("Scheduler history unavailable; scheduled execution is disabled", extra={"event": "scheduler.history_unavailable"})
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self) -> None:
        if not SCHEDULE_FILE.exists():
            return
        try:
            data = load_json(SCHEDULE_FILE, encrypt=True)
            if not isinstance(data, list):
                raise ValueError("scheduled jobs document must be a JSON array")
            normalized: List[Dict[str, Any]] = []
            recovered = False
            for raw in data:
                if not isinstance(raw, dict) or not raw.get("id"):
                    # Do not silently discard malformed entries from a durable store.
                    raise ValueError("scheduled jobs contain an entry without a valid id")
                job = dict(raw)
                job.setdefault("attempts", 0)
                job.setdefault("last_error", None)
                job.setdefault("status", "pending" if job.get("enabled", True) else "completed")
                repeat = job.get("repeat")
                if repeat is not None:
                    try:
                        if isinstance(repeat, dict) and set(repeat) == {"every_minutes"}:
                            _validated_interval(repeat.get("every_minutes"))
                        elif isinstance(repeat, dict) and repeat.get("type") == "calendar":
                            job["repeat"] = validate_calendar_recurrence(repeat)
                        else:
                            raise ValueError("repeat must be a valid interval or calendar recurrence")
                    except ValueError as error:
                        job["status"] = "failed"
                        job["enabled"] = False
                        job["last_error"] = f"Invalid persisted recurrence metadata: {error}; manual review required"
                        recovered = True
                if job["status"] == "running":
                    execution_id = str(job.get("execution_id") or "")
                    try:
                        interrupted = self.history.recover_interrupted(execution_id) if self.history else None
                    except Exception as history_error:
                        interrupted = None
                        self.history_error = type(history_error).__name__
                    job["recovered_after_crash"] = True
                    recovered = True
                    history_status = interrupted.get("status") if interrupted else None
                    if history_status == "succeeded":
                        # The side effect and history checkpoint succeeded; reconcile the
                        # scheduler checkpoint so the completed occurrence is not replayed.
                        job["last_run"] = interrupted.get("finished_at") or _now().isoformat()
                        job["attempts"] = 0
                        job["last_error"] = None
                        job["execution_waiting"] = False
                        job.pop("started_at", None)
                        repeat_rule = job.get("repeat")
                        interval = (repeat_rule or {}).get("every_minutes") if isinstance(repeat_rule, dict) else None
                        if interval is not None:
                            try:
                                interval = _validated_interval(interval)
                                job["run_at"] = (_now() + timedelta(minutes=interval)).isoformat()
                                job["execution_id"] = str(uuid.uuid4())
                                job["status"] = "pending"
                                job["enabled"] = True
                            except ValueError:
                                job["status"] = "failed"
                                job["enabled"] = False
                                job["last_error"] = "Invalid recurrence after interrupted success; manual review required"
                        elif isinstance(repeat_rule, dict) and repeat_rule.get("type") == "calendar":
                            job["run_at"] = next_calendar_occurrence(repeat_rule, datetime.now().astimezone()).isoformat()
                            job["execution_id"] = str(uuid.uuid4())
                            job["status"] = "pending"
                            job["enabled"] = True
                        else:
                            job["status"] = "completed"
                            job["enabled"] = False
                    elif history_status == "retrying":
                        # Handler had already returned a retryable failure; resume its retry.
                        job["status"] = "pending"
                        job["enabled"] = True
                        job.pop("started_at", None)
                    elif history_status == "deferred":
                        # The execution ledger will determine whether the other owner is
                        # still alive; keep the same execution_id and retry after a short wait.
                        job["status"] = "pending"
                        job["enabled"] = True
                        job["execution_waiting"] = True
                        job["attempts"] = max(0, int(job.get("attempts", 1)) - 1)
                        job["run_at"] = (_now() + timedelta(minutes=1)).isoformat()
                        job.pop("started_at", None)
                    else:
                        # A started/missing/uncertain/terminal-failure record cannot prove
                        # that replay is safe. Stop and require explicit operator review.
                        job["status"] = "failed"
                        job["enabled"] = False
                        job["execution_waiting"] = False
                        job["last_error"] = (
                            f"Interrupted execution has recorded outcome {history_status!r}; "
                            "automatic replay blocked. Review execution history before rescheduling."
                            if history_status else
                            "Interrupted execution outcome cannot be verified; automatic replay blocked. "
                            "Review execution history before rescheduling."
                        )
                        job.pop("started_at", None)
                if job["status"] not in {"pending", "running", "completed", "failed", "cancelled"}:
                    job["status"] = "failed"
                    job["enabled"] = False
                    job["last_error"] = "Unsupported persisted scheduler status; manual review required"
                    recovered = True
                if job.get("status") == "pending":
                    try:
                        _run_at_timestamp(job.get("run_at"))
                    except (TypeError, ValueError, OverflowError, OSError):
                        job["status"] = "failed"
                        job["enabled"] = False
                        job["last_error"] = "Invalid persisted run_at timestamp; job disabled for manual review"
                        recovered = True
                # Stable across retries and crash recovery; a new interval occurrence gets a new ID.
                if job.get("status") in {"pending", "running"} and not job.get("execution_id"):
                    job["execution_id"] = str(uuid.uuid4())
                normalized.append(job)
        except DataProtectionError:
            # Authentication failures must not be treated as an empty schedule.
            raise
        except Exception as error:
            try:
                stamp = _now().strftime("%Y%m%d_%H%M%S_%f")
                backup = SCHEDULE_FILE.with_name(f"{SCHEDULE_FILE.name}.corrupt.{stamp}.bak")
                # Same-directory rename atomically removes the corrupt active file
                # only after the quarantine destination has been selected.
                SCHEDULE_FILE.replace(backup)
                logger.exception("Corrupt schedule preserved at %s", backup, extra={"event": "scheduler.corrupt_store_preserved"})
            except Exception as backup_error:
                logger.exception("Schedule load and backup recovery both failed (original error: %s)", type(error).__name__, extra={"event": "scheduler.backup_failed"})
            with self._lock:
                self.jobs = []
            return
        with self._lock:
            self.jobs = normalized
        if recovered or normalized != data:
            try:
                self.save()
            except Exception as error:
                logger.exception("Scheduler recovery checkpoint failed", extra={"event": "scheduler.recovery_checkpoint_failed"})

    def save(self) -> None:
        with self._lock:
            atomic_write_json(SCHEDULE_FILE, list(self.jobs), indent=2, ensure_ascii=False, encrypt=True)

    def add_once(self, prompt: str, run_at: datetime, label: str = "") -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Scheduled prompt cannot be empty")
        job = {
            "id": str(uuid.uuid4()), "prompt": prompt,
            "label": label or prompt[:60], "run_at": run_at.isoformat(),
            "repeat": None, "enabled": True, "status": "pending",
            "attempts": 0, "last_run": None, "last_error": None,
            "execution_id": str(uuid.uuid4()),
        }
        with self._lock:
            self.jobs.append(job)
            self.save()
        return dict(job)

    def add_interval(self, prompt: str, every_minutes: int, label: str = "") -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Scheduled prompt cannot be empty")
        every_minutes = _validated_interval(every_minutes)
        job = {
            "id": str(uuid.uuid4()), "prompt": prompt,
            "label": label or prompt[:60],
            "run_at": (_now() + timedelta(minutes=every_minutes)).isoformat(),
            "repeat": {"every_minutes": every_minutes}, "enabled": True,
            "status": "pending", "attempts": 0, "last_run": None, "last_error": None,
            "execution_id": str(uuid.uuid4()),
        }
        with self._lock:
            self.jobs.append(job)
            self.save()
        return dict(job)

    def add_calendar(
        self, prompt: str, recurrence: Dict[str, Any], label: str = ""
    ) -> Dict[str, Any]:
        """Create a daily, weekly or monthly wall-clock schedule in an IANA timezone."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("Scheduled prompt cannot be empty")
        rule = validate_calendar_recurrence(recurrence)
        first_run = next_calendar_occurrence(rule, _now())
        job = {
            "id": str(uuid.uuid4()), "prompt": prompt,
            "label": label or prompt[:60], "run_at": first_run.isoformat(),
            "repeat": rule, "enabled": True, "status": "pending",
            "attempts": 0, "last_run": None, "last_error": None,
            "execution_id": str(uuid.uuid4()),
        }
        with self._lock:
            self.jobs.append(job)
            self.save()
        return dict(job)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not job or job.get("status") in {"completed", "cancelled", "running"} or job.get("execution_waiting"):
                return False
            previous = dict(job)
            job["enabled"] = False
            job["status"] = "cancelled"
            try:
                self.save()
            except Exception:
                job.clear()
                job.update(previous)
                raise
            return True

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self.jobs]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="VoiceAssistantScheduler")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """Request shutdown and wait up to ``timeout`` seconds.

        Returns True only when the worker has actually stopped. A running
        handler is synchronous and cannot be force-killed safely; False means
        shutdown is still waiting on that handler and callers must not assume
        its side effects have been cancelled.
        """
        timeout = max(0.0, float(timeout))
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return not thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = _now()
            with self._lock:
                due = [dict(job) for job in self.jobs if job.get("enabled")
                       and job.get("status") == "pending" and self._is_due(job, now)]
            for job in due:
                if self._stop.is_set():
                    break
                try:
                    self._fire(job)
                except Exception as error:
                    logger.exception("Scheduler execution cycle failed for job %s", job.get("id"), extra={"event": "scheduler.execution_cycle_failed"})
            self._stop.wait(1.0)

    @staticmethod
    def _is_due(job: Dict[str, Any], now: datetime) -> bool:
        try:
            return _run_at_timestamp(job["run_at"]) <= now.timestamp()
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return False

    def _fire(self, job: Dict[str, Any]) -> None:
        job_id = job.get("id")
        with self._lock:
            live = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not live or not live.get("enabled") or live.get("status") != "pending":
                return
            try:
                repeat = live.get("repeat")
                if repeat is not None:
                    if isinstance(repeat, dict) and set(repeat) == {"every_minutes"}:
                        _validated_interval(repeat.get("every_minutes"))
                    elif isinstance(repeat, dict) and repeat.get("type") == "calendar":
                        live["repeat"] = validate_calendar_recurrence(repeat)
                    else:
                        raise ValueError("repeat must be a valid interval or calendar recurrence")
            except ValueError as error:
                live["status"] = "failed"
                live["enabled"] = False
                live["last_error"] = f"Invalid recurrence metadata: {error}; manual review required"
                self.save()
                return
            live["status"] = "running"
            live["attempts"] = int(live.get("attempts", 0)) + 1
            live["started_at"] = _now().isoformat()
            try:
                self.save()
            except Exception:
                live["status"] = "pending"
                live["attempts"] = max(0, int(live.get("attempts", 1)) - 1)
                live.pop("started_at", None)
                raise
            fired_job = dict(live)

        # Persist the occurrence identity before any handler can cause a side effect.
        if self.history is None:
            history_error = self.history_error or "SchedulerHistoryUnavailable"
        else:
            try:
                self.history.record_started(fired_job)
                history_error = None
            except Exception as error:
                history_error = type(error).__name__
        if history_error:
            with self._lock:
                live = next((item for item in self.jobs if item.get("id") == job_id), None)
                if live:
                    live["status"] = "failed"
                    live["enabled"] = False
                    live["last_error"] = "Execution history unavailable; action was not started. Manual review required."
                    try:
                        self.save()
                    except Exception:
                        logger.exception("Could not persist fail-closed scheduler state", extra={"event": "scheduler.history_fail_closed_checkpoint_failed"})
            logger.error("Scheduled action blocked because execution history could not be checkpointed (%s)", history_error, extra={"event": "scheduler.history_start_failed"})
            return

        succeeded = True
        retryable = True
        defer = False
        error_text = None
        if self.on_job:
            try:
                outcome = self.on_job(fired_job)
                if isinstance(outcome, JobExecutionResult):
                    succeeded = outcome.success
                    retryable = outcome.retryable
                    defer = outcome.defer
                    error_text = (outcome.message or "")[:1000] or None
                else:
                    succeeded = outcome is not False
            except Exception as error:
                succeeded = False
                retryable = True
                error_text = f"{type(error).__name__}: {error}"[:1000]
                logger.exception("Scheduled job handler failed", extra={"event": "scheduler.handler_failed"})

        with self._lock:
            live = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not live:
                return
            execution_id_before_completion = live.get("execution_id")
            live.pop("started_at", None)
            if defer:
                # The same invocation is still active; poll it later without
                # consuming retry budget or changing its idempotency key.
                live["status"] = "pending"
                live["enabled"] = True
                live["attempts"] = max(0, int(live.get("attempts", 1)) - 1)
                live["run_at"] = (_now() + timedelta(minutes=1)).isoformat()
                live["last_error"] = error_text or "Scheduled action is still in progress; waiting for its outcome"
                live["execution_waiting"] = True
            else:
                live["last_run"] = _now().isoformat()
                live["execution_waiting"] = False
                if succeeded:
                    live["last_error"] = None
                    live["attempts"] = 0
                    repeat_rule = live.get("repeat")
                    interval = (repeat_rule or {}).get("every_minutes")
                    if interval is not None:
                        interval = _validated_interval(interval)
                        live["run_at"] = (_now() + timedelta(minutes=interval)).isoformat()
                        live["execution_id"] = str(uuid.uuid4())
                        live["status"] = "pending"
                        live["enabled"] = True
                    elif isinstance(repeat_rule, dict) and repeat_rule.get("type") == "calendar":
                        live["run_at"] = next_calendar_occurrence(
                            repeat_rule, datetime.now().astimezone()
                        ).isoformat()
                        live["execution_id"] = str(uuid.uuid4())
                        live["status"] = "pending"
                        live["enabled"] = True
                    else:
                        live["status"] = "completed"
                        live["enabled"] = False
                else:
                    live["last_error"] = error_text or "Handler reported failure"
                    attempts = int(live.get("attempts", 1))
                    if not retryable:
                        live["status"] = "failed"
                        live["enabled"] = False
                    elif attempts < MAX_ATTEMPTS:
                        delay = RETRY_DELAYS_MINUTES[min(attempts - 1, len(RETRY_DELAYS_MINUTES) - 1)]
                        live["run_at"] = (_now() + timedelta(minutes=delay)).isoformat()
                        live["status"] = "pending"
                        live["enabled"] = True
                    else:
                        live["status"] = "failed"
                        live["enabled"] = False
            # History is a separate durable audit checkpoint. Never store raw prompts
            # or exception text; diagnostics are categorical and bounded.
            history_status = (
                "deferred" if defer else
                "succeeded" if succeeded else
                "retrying" if retryable and int(live.get("attempts", 1)) < MAX_ATTEMPTS else
                "uncertain" if not retryable else
                "failed"
            )
            history_diagnostic = (
                "execution deferred" if defer else
                "handler succeeded" if succeeded else
                "handler outcome uncertain" if history_status == "uncertain" else
                "retry scheduled" if history_status == "retrying" else
                "handler failed"
            )
            try:
                self.history.record_outcome(
                    str(execution_id_before_completion), history_status, history_diagnostic
                )
            except Exception as history_error:
                live["status"] = "failed"
                live["enabled"] = False
                live["last_error"] = "Execution history checkpoint failed; outcome requires manual review."
                try:
                    self.save()
                except Exception:
                    logger.exception("Could not persist history failure state", extra={"event": "scheduler.history_outcome_checkpoint_failed"})
                logger.exception("Scheduled action outcome could not be recorded", extra={"event": "scheduler.history_outcome_failed"})
                return
            try:
                self.save()
            except Exception as error:
                live["status"] = "pending"
                live["enabled"] = True
                live["run_at"] = (_now() + timedelta(minutes=1)).isoformat()
                live["last_error"] = f"Completion checkpoint failed: {error}"[:1000]
                # The occurrence is still pending; retain its idempotency key on replay.
                if execution_id_before_completion:
                    live["execution_id"] = execution_id_before_completion
                logger.exception("Scheduler completion checkpoint failed for job %s", job_id, extra={"event": "scheduler.completion_checkpoint_failed"})
