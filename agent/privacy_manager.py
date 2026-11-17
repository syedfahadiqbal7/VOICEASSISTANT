"""User-controlled retention preview and purge for private runtime stores.

Retention is disabled by default and never runs automatically at application
startup. Purging requires an explicit confirmation from the caller.
"""
from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from config import BASE_DIR
from agent.data_protection import DataProtectionError
from agent.storage import atomic_write_json, load_json

RETENTION_FILE = Path(BASE_DIR) / "history" / "data_retention.json"
DEFAULT_RETENTION: Dict[str, Any] = {
    "version": 1,
    "conversation_days": None,
    "audit_days": None,
    "memory_days": None,
    "completed_job_days": None,
}
_ALLOWED_SETTINGS = {
    "conversation_days",
    "audit_days",
    "memory_days",
    "completed_job_days",
}
_TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _expired(value: Any, days: Optional[int], now: datetime) -> bool:
    if days is None:
        return False
    timestamp = _parse_timestamp(value)
    return timestamp is not None and timestamp < now - timedelta(days=days)


class PrivacyManager:
    """Manage opt-in retention settings and a preview-before-purge workflow."""

    def __init__(self, base_dir: Path | str | None = None, policy_path: Path | str | None = None):
        self.base_dir = Path(base_dir or BASE_DIR).resolve()
        self.history_dir = self.base_dir / "history"
        if self.history_dir.is_symlink():
            raise DataProtectionError("Runtime history directory must not be a symbolic link")
        self.policy_path = Path(policy_path) if policy_path else self.history_dir / "data_retention.json"
        if self.policy_path.is_symlink():
            raise DataProtectionError("Data retention policy must not be a symbolic link")
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = copy.deepcopy(DEFAULT_RETENTION)
        self.load_policy()

    def load_policy(self) -> None:
        if self.policy_path.is_symlink():
            raise DataProtectionError("Data retention policy must not be a symbolic link")
        raw = load_json(self.policy_path, default=None, encrypt=True)
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ValueError("data retention policy must be a JSON object")
        merged = copy.deepcopy(DEFAULT_RETENTION)
        merged.update(raw)
        for key in _ALLOWED_SETTINGS:
            value = merged.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 36500):
                raise ValueError(f"Invalid retention setting {key}: expected 1..36500 days or null")
        self.policy = merged

    def save_policy(self) -> None:
        atomic_write_json(self.policy_path, self.policy, indent=2, ensure_ascii=False, encrypt=True)

    def get_policy(self) -> Dict[str, Any]:
        return copy.deepcopy(self.policy)

    def set_retention(self, **settings: Optional[int]) -> Dict[str, Any]:
        unknown = set(settings) - _ALLOWED_SETTINGS
        if unknown:
            raise ValueError(f"Unknown retention settings: {', '.join(sorted(unknown))}")
        updated = copy.deepcopy(self.policy)
        for key, value in settings.items():
            if value is None or value == 0:
                updated[key] = None
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 36500:
                raise ValueError(f"{key} must be 0 (disabled) or between 1 and 36500 days")
            else:
                updated[key] = value
        self.policy = updated
        self.save_policy()
        return self.get_policy()

    def preview(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now()
        result: Dict[str, Any] = {
            "conversation_history": {"delete_file": False},
            "memory": {"delete_file": False},
            "audit_log": {"delete_entries": 0, "keep_entries": 0},
            "scheduled_jobs": {"delete_terminal_jobs": 0, "keep_jobs": 0},
        }

        conversation = self.history_dir / "conversation_history.json"
        days = self.policy.get("conversation_days")
        if days is not None and conversation.is_file():
            if conversation.is_symlink():
                raise DataProtectionError("Conversation history must not be a symbolic link")
            age = datetime.fromtimestamp(conversation.stat().st_mtime)
            result["conversation_history"]["delete_file"] = age < now - timedelta(days=days)

        memory = self.history_dir / "agent_memory.json"
        days = self.policy.get("memory_days")
        if days is not None and memory.is_file():
            if memory.is_symlink():
                raise DataProtectionError("Memory store must not be a symbolic link")
            age = datetime.fromtimestamp(memory.stat().st_mtime)
            result["memory"]["delete_file"] = age < now - timedelta(days=days)

        days = self.policy.get("audit_days")
        audit_path = self.history_dir / "audit_log.json"
        if days is not None and audit_path.is_file():
            if audit_path.is_symlink():
                raise DataProtectionError("Audit log must not be a symbolic link")
            entries = load_json(audit_path, encrypt=True)
            if not isinstance(entries, list):
                raise ValueError("audit log must be a JSON array")
            expired = sum(1 for entry in entries if isinstance(entry, dict) and _expired(entry.get("timestamp"), days, now))
            result["audit_log"] = {
                "delete_entries": expired,
                "keep_entries": len(entries) - expired,
            }

        days = self.policy.get("completed_job_days")
        schedule_path = self.history_dir / "scheduled_jobs.json"
        if days is not None and schedule_path.is_file():
            if schedule_path.is_symlink():
                raise DataProtectionError("Scheduled jobs store must not be a symbolic link")
            jobs = load_json(schedule_path, encrypt=True)
            if not isinstance(jobs, list):
                raise ValueError("scheduled jobs must be a JSON array")
            expired = 0
            for job in jobs:
                if not isinstance(job, dict) or job.get("status") not in _TERMINAL_JOB_STATES:
                    continue
                timestamp = job.get("last_run") or job.get("run_at")
                if _expired(timestamp, days, now):
                    expired += 1
            result["scheduled_jobs"] = {
                "delete_terminal_jobs": expired,
                "keep_jobs": len(jobs) - expired,
            }
        return result

    def purge(self, *, confirm: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
        if not confirm:
            raise PermissionError("Retention purge requires explicit confirm=True")
        preview = self.preview(now=now)
        deleted: Dict[str, int] = {
            "conversation_history_files": 0,
            "memory_files": 0,
            "audit_entries": 0,
            "scheduled_jobs": 0,
        }

        conversation = self.history_dir / "conversation_history.json"
        if preview["conversation_history"]["delete_file"] and conversation.exists():
            conversation.unlink()
            deleted["conversation_history_files"] = 1

        memory = self.history_dir / "agent_memory.json"
        if preview["memory"]["delete_file"] and memory.exists():
            memory.unlink()
            deleted["memory_files"] = 1

        audit_path = self.history_dir / "audit_log.json"
        if preview["audit_log"]["delete_entries"]:
            entries = load_json(audit_path, encrypt=True)
            days = self.policy["audit_days"]
            now_value = now or datetime.now()
            kept = [
                entry for entry in entries
                if not (isinstance(entry, dict) and _expired(entry.get("timestamp"), days, now_value))
            ]
            atomic_write_json(audit_path, kept, indent=2, ensure_ascii=False, encrypt=True)
            deleted["audit_entries"] = len(entries) - len(kept)

        schedule_path = self.history_dir / "scheduled_jobs.json"
        if preview["scheduled_jobs"]["delete_terminal_jobs"]:
            jobs = load_json(schedule_path, encrypt=True)
            days = self.policy["completed_job_days"]
            now_value = now or datetime.now()
            kept = []
            for job in jobs:
                is_expired_terminal = (
                    isinstance(job, dict)
                    and job.get("status") in _TERMINAL_JOB_STATES
                    and _expired(job.get("last_run") or job.get("run_at"), days, now_value)
                )
                if not is_expired_terminal:
                    kept.append(job)
            atomic_write_json(schedule_path, kept, indent=2, ensure_ascii=False, encrypt=True)
            deleted["scheduled_jobs"] = len(jobs) - len(kept)

        return {"ok": True, "preview": preview, "deleted": deleted}
