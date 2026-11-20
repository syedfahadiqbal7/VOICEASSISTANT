"""Durable at-most-once invocation guard for scheduled tool side effects.

A persisted "started" record is intentionally treated as ambiguous after its
owner process exits. The application cannot know whether an external side
effect completed immediately before a crash, so it must not blindly replay it.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from config import BASE_DIR
from agent.storage import atomic_write_json, load_json

LEDGER_FILE = BASE_DIR / "history" / "scheduled_execution_ledger.json"
MAX_ENTRIES = 20000


class ScheduledExecutionLedgerError(RuntimeError):
    """The durable execution ledger is unavailable or unsafe to modify."""


@contextmanager
def _interprocess_file_lock(lock_path: Path) -> Iterator[None]:
    """Serialize ledger read/modify/write operations across desktop processes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
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
                raise RuntimeError("Scheduled execution ledger requires OS file locking")
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _process_start_time(pid: int) -> Optional[float]:
    try:
        import psutil
        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


_CURRENT_PID = os.getpid()
_CURRENT_PROCESS_START = _process_start_time(_CURRENT_PID)


class ScheduledExecutionLedger:
    """Persist a stable execution ID before invoking a scheduled tool.

    States:
      started   — invocation claimed and owned by a live process, or outcome
                  unknown after that process exits.
      succeeded — tool and required post-execution bookkeeping completed.
      uncertain — invocation began but success could not be durably confirmed.

    Only a new ID may be claimed. Existing succeeded IDs are safe no-ops;
    started IDs are in-progress only while their recorded owner process lives.
    """

    def __init__(self, path: Optional[Path | str] = None, max_entries: int = MAX_ENTRIES):
        self.path = Path(path or LEDGER_FILE)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._active: set[str] = set()
        try:
            with self._guard():
                self._entries = self._read_entries()
        except Exception as error:
            raise ScheduledExecutionLedgerError(
                f"Scheduled execution ledger could not be safely loaded: {error}"
            ) from error

    @contextmanager
    def _guard(self):
        with self._lock:
            with _interprocess_file_lock(self.lock_path):
                yield

    def _read_entries(self) -> Dict[str, Dict[str, Any]]:
        document = load_json(
            self.path,
            default={"version": 1, "entries": {}},
            encrypt=True,
        )
        if document == {}:
            document = {"version": 1, "entries": {}}
        if not isinstance(document, dict) or document.get("version") != 1:
            raise ValueError("unsupported scheduled execution ledger format")
        entries = document.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("scheduled execution ledger entries must be an object")
        for execution_id, entry in entries.items():
            if not isinstance(execution_id, str) or not isinstance(entry, dict):
                raise ValueError("scheduled execution ledger contains an invalid entry")
            if entry.get("status") not in {"started", "succeeded", "uncertain"}:
                raise ValueError("scheduled execution ledger contains an unknown state")
        return entries

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat()

    def _save(self) -> None:
        atomic_write_json(
            self.path,
            {"version": 1, "entries": self._entries},
            indent=2,
            ensure_ascii=False,
            encrypt=True,
        )

    @staticmethod
    def _owner_is_alive(entry: Dict[str, Any]) -> bool:
        try:
            pid = int(entry.get("owner_pid"))
            started = float(entry.get("owner_process_start"))
            actual = _process_start_time(pid)
            return actual is not None and abs(actual - started) < 0.01
        except (TypeError, ValueError):
            return False

    def claim(self, execution_id: str, tool_name: str) -> str:
        """Return claimed, succeeded, in_progress or uncertain.

        The started checkpoint is written before the caller may invoke a tool.
        """
        key = str(execution_id or "").strip()
        tool = str(tool_name or "").strip()
        if not key or len(key) > 128 or not tool or len(tool) > 128:
            raise ScheduledExecutionLedgerError("A valid execution ID and tool name are required")

        with self._guard():
            self._entries = self._read_entries()
            existing = self._entries.get(key)
            if existing:
                status = existing.get("status")
                if status == "succeeded":
                    return "succeeded"
                if status == "started":
                    if key in self._active or self._owner_is_alive(existing):
                        return "in_progress"
                    previous = dict(existing)
                    existing["status"] = "uncertain"
                    existing["finished_at"] = self._timestamp()
                    existing.pop("owner_pid", None)
                    existing.pop("owner_process_start", None)
                    existing["detail"] = "Previous process ended before outcome was confirmed"
                    try:
                        self._save()
                    except Exception as error:
                        existing.clear()
                        existing.update(previous)
                        raise ScheduledExecutionLedgerError(
                            f"Could not checkpoint ambiguous scheduled execution: {error}"
                        ) from error
                    return "uncertain"
                return "uncertain"

            if len(self._entries) >= self.max_entries:
                raise ScheduledExecutionLedgerError(
                    "Scheduled execution ledger is full; refusing an untracked side effect"
                )

            entry = {
                "status": "started",
                "tool": tool,
                "started_at": self._timestamp(),
                "owner_pid": _CURRENT_PID,
                "owner_process_start": _CURRENT_PROCESS_START,
            }
            self._entries[key] = entry
            try:
                self._save()
            except Exception as error:
                self._entries.pop(key, None)
                raise ScheduledExecutionLedgerError(
                    f"Could not persist scheduled execution claim: {error}"
                ) from error
            self._active.add(key)
            return "claimed"

    def complete(self, execution_id: str, *, success: bool, detail: str = "") -> None:
        """Durably record success or an uncertain outcome and release ownership."""
        key = str(execution_id or "").strip()
        with self._guard():
            self._entries = self._read_entries()
            entry = self._entries.get(key)
            if (
                not entry
                or entry.get("status") != "started"
                or key not in self._active
                or int(entry.get("owner_pid", -1)) != _CURRENT_PID
            ):
                raise ScheduledExecutionLedgerError(
                    "Scheduled execution is not owned by this process"
                )
            previous = dict(entry)
            entry["status"] = "succeeded" if success else "uncertain"
            entry["finished_at"] = self._timestamp()
            entry.pop("owner_pid", None)
            entry.pop("owner_process_start", None)
            if detail:
                entry["detail"] = " ".join(str(detail).split())[:500]
            try:
                self._save()
            except Exception as error:
                entry.clear()
                entry.update(previous)
                raise ScheduledExecutionLedgerError(
                    f"Could not persist scheduled execution outcome: {error}"
                ) from error
            finally:
                self._active.discard(key)

    def get(self, execution_id: str) -> Optional[Dict[str, Any]]:
        with self._guard():
            self._entries = self._read_entries()
            entry = self._entries.get(str(execution_id or "").strip())
            return dict(entry) if entry else None
