# agent/scheduler.py
"""
Phase 2 — Background / scheduled agent jobs (in-process).
Simple JSON-backed scheduler; runs due jobs while desktop app is open.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import BASE_DIR

SCHEDULE_FILE = BASE_DIR / "history" / "scheduled_jobs.json"


class JobScheduler:
    def __init__(self, on_job: Optional[Callable[[Dict[str, Any]], None]] = None):
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
            with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                with self._lock:
                    self.jobs = data
        except Exception:
            self.jobs = []

    def save(self) -> None:
        try:
            from agent.storage import atomic_write_json
            with self._lock:
                atomic_write_json(SCHEDULE_FILE, list(self.jobs), indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Scheduler] save failed: {e}")

    def add_once(self, prompt: str, run_at: datetime, label: str = "") -> Dict[str, Any]:
        job = {
            "id": str(uuid.uuid4())[:8],
            "prompt": prompt,
            "label": label or prompt[:60],
            "run_at": run_at.isoformat(),
            "repeat": None,
            "enabled": True,
            "last_run": None,
        }
        with self._lock:
            self.jobs.append(job)
        self.save()
        return job

    def add_interval(self, prompt: str, every_minutes: int, label: str = "") -> Dict[str, Any]:
        every_minutes = max(1, int(every_minutes))
        job = {
            "id": str(uuid.uuid4())[:8],
            "prompt": prompt,
            "label": label or prompt[:60],
            "run_at": (datetime.now() + timedelta(minutes=every_minutes)).isoformat(),
            "repeat": {"every_minutes": every_minutes},
            "enabled": True,
            "last_run": None,
        }
        with self._lock:
            self.jobs.append(job)
        self.save()
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if j.get("id") != job_id]
            changed = len(self.jobs) < before
        self.save()
        return changed

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self.jobs]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            due = []
            with self._lock:
                for job in self.jobs:
                    if not job.get("enabled"):
                        continue
                    try:
                        run_at = datetime.fromisoformat(job["run_at"])
                    except Exception:
                        continue
                    if run_at <= now:
                        due.append(dict(job))
            for job in due:
                self._fire(job)
            time.sleep(5)

    def _fire(self, job: Dict[str, Any]) -> None:
        job_id = job.get("id")
        with self._lock:
            live = next((item for item in self.jobs if item.get("id") == job_id), None)
            if not live or not live.get("enabled"):
                return
            live["last_run"] = datetime.now().isoformat()
            if live.get("repeat") and live["repeat"].get("every_minutes"):
                mins = int(live["repeat"]["every_minutes"])
                live["run_at"] = (datetime.now() + timedelta(minutes=mins)).isoformat()
            else:
                live["enabled"] = False
            fired_job = dict(live)
        self.save()
        if self.on_job:
            try:
                self.on_job(fired_job)
            except Exception as e:
                print(f"[Scheduler] job handler error: {e}")
