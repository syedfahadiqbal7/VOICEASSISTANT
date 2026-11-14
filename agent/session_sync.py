# agent/session_sync.py
"""
Phase 2 — Cross-device session continuity (mobile ↔ desktop).
Broadcasts session_state (status, plan, undo, jobs) to connected mobiles.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class SessionSync:
    def __init__(self):
        self._bridge = None
        self.state: Dict[str, Any] = {
            "active_plan": None,
            "last_user_text": None,
            "last_status": None,
            "undo_available": False,
            "jobs_count": 0,
            "jobs": [],  # full job list for mobile Schedules UI
        }

    def attach_bridge(self, bridge) -> None:
        self._bridge = bridge

    def update(self, **kwargs) -> None:
        self.state.update({k: v for k, v in kwargs.items() if v is not None})
        self._broadcast()

    def set_status(self, message: str) -> None:
        self.update(last_status=message)

    def set_user_text(self, text: str) -> None:
        self.update(last_user_text=text)

    def set_plan(self, plan: Optional[dict]) -> None:
        self.update(active_plan=plan)

    def set_undo_available(self, available: bool) -> None:
        self.update(undo_available=bool(available))

    def set_jobs_count(self, n: int) -> None:
        self.update(jobs_count=int(n))

    def set_jobs(self, jobs: List[Dict[str, Any]]) -> None:
        """Push full scheduled jobs list (safe subset of fields)."""
        safe = []
        for j in jobs or []:
            if not isinstance(j, dict):
                continue
            safe.append(
                {
                    "id": j.get("id"),
                    "label": j.get("label") or (j.get("prompt") or "")[:80],
                    "prompt": j.get("prompt", ""),
                    "run_at": j.get("run_at"),
                    "repeat": j.get("repeat"),
                    "enabled": j.get("enabled", True),
                    "last_run": j.get("last_run"),
                }
            )
        self.state["jobs"] = safe
        self.state["jobs_count"] = len(safe)
        self._broadcast()

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.state)

    def _broadcast(self) -> None:
        if not self._bridge:
            return
        try:
            from mobile_api.connection_manager import manager

            payload = {"type": "session_state", "state": dict(self.state)}
            runner = getattr(self._bridge, "_run_async", None)
            if callable(runner):
                runner(manager.broadcast(payload))
        except Exception as e:
            print(f"[SessionSync] broadcast skipped: {e}")
