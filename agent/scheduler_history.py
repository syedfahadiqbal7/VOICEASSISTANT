"""Encrypted, bounded history for scheduled execution occurrences.

One record is maintained per stable execution_id. Retries update the same
occurrence instead of creating duplicate history entries. Prompt bodies and
exception messages are deliberately excluded because they may contain secrets.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import BASE_DIR
from agent.storage import atomic_write_json, load_json

HISTORY_FILE = BASE_DIR / "history" / "scheduled_execution_history.json"
MAX_RECORDS = 1000
_ALLOWED_STATUSES = {
    "started", "retrying", "succeeded", "failed", "uncertain", "skipped", "deferred"
}
_TERMINAL_STATUSES = {"succeeded", "failed", "uncertain", "skipped"}
_SAFE_DIAGNOSTICS = {
    "execution deferred", "handler succeeded", "handler outcome uncertain",
    "retry scheduled", "handler failed", "outcome recorded",
}


class SchedulerHistoryError(RuntimeError):
    """History storage is unavailable or contains unsafe data."""


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                raise RuntimeError("Scheduler history requires OS file locking")
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SchedulerExecutionHistory:
    """Durable encrypted occurrence history with bounded retention."""

    def __init__(self, path: Optional[Path | str] = None, max_records: int = MAX_RECORDS):
        self.path = Path(path or HISTORY_FILE)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.max_records = max(1, int(max_records))
        self._lock = threading.RLock()
        try:
            with self._guard():
                self._read()
        except Exception as error:
            raise SchedulerHistoryError(
                f"Scheduled execution history could not be safely loaded: {type(error).__name__}"
            ) from error

    @contextmanager
    def _guard(self):
        with self._lock:
            with _file_lock(self.lock_path):
                yield

    def _read(self) -> List[Dict[str, Any]]:
        document = load_json(
            self.path,
            default={"version": 1, "records": []},
            encrypt=True,
        )
        if document == {}:
            document = {"version": 1, "records": []}
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or not isinstance(document.get("records"), list)
        ):
            raise ValueError("Unsupported scheduler history document")
        records = document["records"]
        if len(records) > self.max_records:
            records = records[-self.max_records:]
            self._write(records)
        seen_ids = set()
        for record in records:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("execution_id"), str)
                or not record["execution_id"]
                or record.get("status") not in _ALLOWED_STATUSES
            ):
                raise ValueError("Malformed scheduler history record")
            if record["execution_id"] in seen_ids:
                raise ValueError("Duplicate scheduler history execution_id")
            seen_ids.add(record["execution_id"])
        return records

    def _write(self, records: List[Dict[str, Any]]) -> None:
        bounded = records[-self.max_records:]
        atomic_write_json(
            self.path,
            {"version": 1, "records": bounded},
            indent=2,
            ensure_ascii=False,
            encrypt=True,
        )

    def record_started(self, job: Dict[str, Any]) -> None:
        execution_id = str(job.get("execution_id") or "").strip()
        job_id = str(job.get("id") or "").strip()
        if not execution_id or not job_id:
            raise SchedulerHistoryError("Scheduled occurrence is missing stable identity")
        with self._guard():
            records = self._read()
            existing = next((r for r in records if r["execution_id"] == execution_id), None)
            if existing and existing.get("status") in _TERMINAL_STATUSES:
                raise SchedulerHistoryError("Scheduled occurrence already has a terminal outcome")
            if existing is None:
                existing = {
                    "job_id": job_id,
                    "execution_id": execution_id,
                    "label": "Scheduled task",
                    "scheduled_at": str(job.get("run_at") or "")[:64],
                    "started_at": _utc_now(),
                    "finished_at": None,
                    "status": "started",
                    "attempts": 0,
                    "diagnostic": None,
                }
                records.append(existing)
            existing["attempts"] = max(0, int(existing.get("attempts", 0))) + 1
            existing["status"] = "started"
            existing["finished_at"] = None
            self._write(records)

    def record_outcome(
        self,
        execution_id: str,
        status: str,
        diagnostic: Optional[str] = None,
    ) -> None:
        if status not in _ALLOWED_STATUSES - {"started"}:
            raise ValueError("Unsupported scheduler history outcome")
        with self._guard():
            records = self._read()
            record = next((r for r in records if r["execution_id"] == execution_id), None)
            if record is None:
                raise SchedulerHistoryError("Scheduled occurrence history was not started")
            if record.get("status") in _TERMINAL_STATUSES:
                if record["status"] == status:
                    return
                raise SchedulerHistoryError("Terminal scheduled occurrence cannot be rewritten")
            record["status"] = status
            record["finished_at"] = _utc_now() if status in _TERMINAL_STATUSES else None
            # Diagnostics are categorical, bounded and must never contain raw prompts/errors.
            record["diagnostic"] = diagnostic if isinstance(diagnostic, str) and diagnostic in _SAFE_DIAGNOSTICS else "outcome recorded" if diagnostic else None
            self._write(records)

    def list_records(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), self.max_records))
        with self._guard():
            records = self._read()
            return [dict(record) for record in reversed(records[-safe_limit:])]
