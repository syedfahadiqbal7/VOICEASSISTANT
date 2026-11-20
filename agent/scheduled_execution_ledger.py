"""Durable at-most-once invocation guard for scheduled tool side effects.

A persisted "started" record is intentionally treated as ambiguous after a
process restart. The application cannot know whether an external side effect
completed immediately before a crash, so it must not blindly replay it.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import BASE_DIR
from agent.storage import atomic_write_json, load_json

LEDGER_FILE = BASE_DIR / "history" / "scheduled_execution_ledger.json"
MAX_ENTRIES = 20000


class ScheduledExecutionLedgerError(RuntimeError):
    """The durable execution ledger is unavailable or unsafe to modify."""


class ScheduledExecutionLedger:
    """Persist a stable execution ID before invoking a scheduled tool.

    States:
      started   — invocation was claimed; currently active in this process or
                  outcome unknown after a restart.
      succeeded — tool and required post-execution bookkeeping completed.
      uncertain — invocation began but success could not be durably confirmed.

    Only a new ID may be claimed. Existing succeeded IDs are safe no-ops;
    started IDs are reported as in-progress only while this process owns them.
    """

    def __init__(self, path: Optional[Path | str] = None, max_entries: int = MAX_ENTRIES):
        self.path = Path(path or LEDGER_FILE)
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._active: set[str] = set()
        try:
            document = load_json(self.path, default={}, encrypt=True)
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
            self._entries: Dict[str, Dict[str, Any]] = entries
        except Exception as error:
            raise ScheduledExecutionLedgerError(
                f"Scheduled execution ledger could not be safely loaded: {error}"
            ) from error

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

    def claim(self, execution_id: str, tool_name: str) -> str:
        """Return claimed, succeeded, in_progress or uncertain.

        The started checkpoint is written before the caller may invoke a tool.
        """
        key = str(execution_id or "").strip()
        tool = str(tool_name or "").strip()
        if not key or len(key) > 128 or not tool or len(tool) > 128:
            raise ScheduledExecutionLedgerError("A valid execution ID and tool name are required")

        with self._lock:
            existing = self._entries.get(key)
            if existing:
                status = existing.get("status")
                if status == "succeeded":
                    return "succeeded"
                if status == "started":
                    if key in self._active:
                        return "in_progress"
                    previous = dict(existing)
                    existing["status"] = "uncertain"
                    existing["finished_at"] = self._timestamp()
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
        with self._lock:
            entry = self._entries.get(key)
            if not entry or entry.get("status") != "started" or key not in self._active:
                raise ScheduledExecutionLedgerError(
                    "Scheduled execution is not owned by this process"
                )
            previous = dict(entry)
            entry["status"] = "succeeded" if success else "uncertain"
            entry["finished_at"] = self._timestamp()
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
        with self._lock:
            entry = self._entries.get(str(execution_id or "").strip())
            return dict(entry) if entry else None
