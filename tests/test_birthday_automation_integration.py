"""End-to-end local birthday reminder scheduling tests."""
from datetime import datetime, timedelta

import pytest

import agent.scheduler as scheduler_module
import agent.scheduler_history as history_module
from agent.scheduler import JobScheduler
from agent.user_profile import UserProfileStore
from agent.conversation_manager import ConversationManager


def _manager(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(scheduler_module, "SCHEDULE_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(history_module, "HISTORY_FILE", tmp_path / "execution-history.json")
    manager = object.__new__(ConversationManager)
    manager.profile = profile
    manager.scheduler = JobScheduler()
    return manager


def test_enable_and_disable_birthday_reminder_schedule(tmp_path, monkeypatch):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({
        "settings": {
            "personal_automations_enabled": True,
            "timezone": "Asia/Dubai",
            "birthday_reminder_time": "08:45",
        },
        "birthdays": [{
            "label": "Family member",
            "month_day": "12-04",
            "reminder_days": [30, 7, 1],
        }],
    })
    manager = _manager(tmp_path, monkeypatch, profile)

    enabled = manager.set_birthday_reminders_enabled(True)
    assert enabled["active"] is True
    jobs = manager.scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["prompt"] == "__automation__:birthday_reminders"
    assert jobs[0]["repeat"] == {
        "type": "calendar",
        "frequency": "daily",
        "local_time": "08:45",
        "timezone": "Asia/Dubai",
    }
    assert profile.get_profile()["settings"]["birthday_reminders_enabled"] is True

    disabled = manager.set_birthday_reminders_enabled(False)
    assert disabled["active"] is False
    assert manager.scheduler.list_jobs()[0]["status"] == "cancelled"
    assert profile.get_profile()["settings"]["birthday_reminders_enabled"] is False


def test_birthday_schedule_requires_master_switch_birthdays_and_timezone(tmp_path, monkeypatch):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({
        "settings": {"personal_automations_enabled": True},
        "birthdays": [{"label": "Family", "month_day": "12-04", "reminder_days": [7]}],
    })
    manager = _manager(tmp_path, monkeypatch, profile)

    with pytest.raises(ValueError, match="settings.timezone"):
        manager.set_birthday_reminders_enabled(True)
    assert manager.scheduler.list_jobs() == []
    assert profile.get_profile()["settings"]["birthday_reminders_enabled"] is False


def test_birthday_handler_delivers_only_due_local_reminders(tmp_path):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({
        "settings": {"timezone": "Asia/Dubai"},
        "birthdays": [{"label": "Family member", "month_day": "11-27", "reminder_days": [1]}],
    })
    manager = object.__new__(ConversationManager)
    manager.profile = profile
    transcript = []
    status = []
    spoken = []
    manager._log = lambda _message: None
    manager.on_transcript = transcript.append
    manager.on_status = lambda message, _color=None: status.append(message)
    manager.on_speak = spoken.append

    # Current test date is fixed by the pure reminder unit tests; inject the
    # calculation at the service boundary here to avoid wall-clock coupling.
    from agent import birthday_reminders
    original = birthday_reminders.due_birthday_reminders
    birthday_reminders.due_birthday_reminders = lambda _profile: ["Family member's birthday is tomorrow (27 Nov)."]
    try:
        result = manager._on_scheduled_job({
            "id": "job-1",
            "execution_id": "exec-1",
            "prompt": "__automation__:birthday_reminders",
        })
    finally:
        birthday_reminders.due_birthday_reminders = original

    assert result is True
    assert len(transcript) == len(status) == len(spoken) == 1
    assert "tomorrow" in transcript[0]
