"""Regression tests for scheduler UI state semantics."""
from agent.scheduler_ui_state import job_display_state


def test_explicit_running_state_is_not_misreported_as_on_or_cancellable():
    assert job_display_state({"status": "running", "enabled": True, "attempts": 2}) == (
        "Running", "attempts: 2", False
    )


def test_pending_state_exposes_cancel_and_retry_diagnostics():
    assert job_display_state({
        "status": "pending", "enabled": True, "attempts": 1,
        "last_error": "Temporary   failure",
    }) == ("Pending", "attempts: 1 | last error: Temporary failure", True)


def test_terminal_states_are_distinguished_and_not_cancellable():
    assert job_display_state({"status": "completed", "enabled": False}) == (
        "Completed", "", False
    )
    assert job_display_state({"status": "failed", "enabled": False, "last_error": "boom"}) == (
        "Failed", "last error: boom", False
    )
    assert job_display_state({"status": "cancelled", "enabled": False}) == (
        "Cancelled", "", False
    )


def test_legacy_jobs_without_status_keep_enabled_fallback():
    assert job_display_state({"enabled": True}) == ("Pending", "", True)
    assert job_display_state({"enabled": False}) == ("Completed", "", False)


def test_unknown_explicit_state_fails_closed_for_cancel_action():
    assert job_display_state({"status": "future-state", "enabled": True}) == (
        "Unknown", "", False
    )


def test_error_detail_is_whitespace_normalized_and_bounded():
    label, detail, can_cancel = job_display_state({
        "status": "pending", "last_error": "x" * 300,
    })
    assert label == "Pending"
    assert len(detail) == len("last error: ") + 180
    assert detail.endswith("...")
    assert can_cancel is True


def test_deferred_pending_execution_is_shown_as_waiting_and_not_cancellable():
    assert job_display_state({
        "status": "pending", "enabled": True, "execution_waiting": True,
        "last_error": "action still running",
    }) == ("Waiting", "last error: action still running", False)
