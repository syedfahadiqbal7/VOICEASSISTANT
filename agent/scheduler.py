# agent/scheduler.py
"""Durable in-process scheduler with crash recovery and bounded retries.

Execution is at-least-once: a crash after an external side effect but before its
completion checkpoint can replay the handler after restart. Side-effecting
handlers should therefore be idempotent where possible.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from config import BASE_DIR
from agent.storage import atomic_write_json, load_json
from agent.data_protection import DataProtectionError

SCHEDULE_FILE = BASE_DIR / "history" / "scheduled_jobs.json"
MAX_ATTEMPTS = 3
RETRY_DELAYS_MINUTES = (1, 5, 15)

logger = logging.getLogger("voiceassistant.scheduler")


def _now() -> datetime:
    return datetime.now()


class JobScheduler:
    def __init__(self, on_job: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self.on_job = on_job
        self.jobs: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
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
                    continue
                job = dict(raw)
                job.setdefault("attempts", 0)
                job.setdefault("last_error", None)
                job.setdefault("status", "pending" if job.get("enabled", True) else "completed")
                if job["status"] == "running":
                    job["status"] = "pending"
                    job["enabled"] = True
                    job["recovered_after_crash"] = True
                    recovered = True
                if job["status"] not in {"pending", "running", "completed", "failed", "cancelled"}:
                    job["status"] = "pending" if job.get("enabled", True) else "completed"
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
                shutil.copy2(SCHEDULE_FILE, backup)
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
        every_minutes = int(every_minutes)
        if every_minutes < 1:
            raise ValueError("Interval must be at least one minute")
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

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not job or job.get("status") in {"completed", "cancelled", "running"}:
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
            return datetime.fromisoformat(str(job["run_at"])) <= now
        except (KeyError, TypeError, ValueError):
            return False

    def _fire(self, job: Dict[str, Any]) -> None:
        job_id = job.get("id")
        with self._lock:
            live = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not live or not live.get("enabled") or live.get("status") != "pending":
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

        succeeded = True
        error_text = None
        if self.on_job:
            try:
                succeeded = self.on_job(fired_job) is not False
            except Exception as error:
                succeeded = False
                error_text = f"{type(error).__name__}: {error}"[:1000]
                logger.exception("Scheduled job handler failed", extra={"event": "scheduler.handler_failed"})

        with self._lock:
            live = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not live:
                return
            execution_id_before_completion = live.get("execution_id")
            live["last_run"] = _now().isoformat()
            live.pop("started_at", None)
            if succeeded:
                live["last_error"] = None
                live["attempts"] = 0
                interval = (live.get("repeat") or {}).get("every_minutes")
                if interval:
                    live["run_at"] = (_now() + timedelta(minutes=max(1, int(interval)))).isoformat()
                    live["execution_id"] = str(uuid.uuid4())
                    live["status"] = "pending"
                    live["enabled"] = True
                else:
                    live["status"] = "completed"
                    live["enabled"] = False
            else:
                live["last_error"] = error_text or "Handler reported failure"
                attempts = int(live.get("attempts", 1))
                if attempts < MAX_ATTEMPTS:
                    delay = RETRY_DELAYS_MINUTES[min(attempts - 1, len(RETRY_DELAYS_MINUTES) - 1)]
                    live["run_at"] = (_now() + timedelta(minutes=delay)).isoformat()
                    live["status"] = "pending"
                    live["enabled"] = True
                else:
                    live["status"] = "failed"
                    live["enabled"] = False
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
