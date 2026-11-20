"""Pure presentation rules for scheduled-job state.

Kept independent of CustomTkinter so status semantics can be regression-tested
without creating a desktop window.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

_STATUS_LABELS = {
    "pending": "Pending",
    "running": "Running",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def job_display_state(job: Dict[str, Any]) -> Tuple[str, str, bool]:
    """Return (human status, diagnostic detail, can_cancel) for a scheduler row.

    Older persisted jobs may not contain status; retain the legacy
    enabled-based interpretation only for those records. Unknown explicit
    states are displayed as Unknown and are never offered a cancel action.
    """
    raw_status = job.get("status")
    if raw_status is None:
        status = "pending" if job.get("enabled", True) else "completed"
    else:
        status = str(raw_status).strip().lower()

    waiting = status == "pending" and bool(job.get("execution_waiting"))
    label = "Waiting" if waiting else _STATUS_LABELS.get(status, "Unknown")
    details = []
    try:
        attempts = max(0, int(job.get("attempts", 0)))
    except (TypeError, ValueError):
        attempts = 0
    if attempts:
        details.append(f"attempts: {attempts}")

    error = job.get("last_error")
    if error:
        safe_error = " ".join(str(error).split())
        if len(safe_error) > 180:
            safe_error = safe_error[:177] + "..."
        details.append(f"last error: {safe_error}")

    return label, " | ".join(details), status == "pending" and not waiting
