"""Integration tests for the locally generated daily language lesson."""
from types import SimpleNamespace

import pytest

import agent.scheduler as scheduler_module
import agent.scheduler_history as history_module
from agent.conversation_manager import ConversationManager
from agent.scheduler import JobScheduler
from agent.user_profile import UserProfileStore


def _manager(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(scheduler_module, "SCHEDULE_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(history_module, "HISTORY_FILE", tmp_path / "execution-history.json")
    manager = object.__new__(ConversationManager)
    manager.profile = profile
    manager.scheduler = JobScheduler()
    return manager


def test_enable_and_disable_daily_language_lesson_schedule(tmp_path, monkeypatch):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({
        "settings": {
            "personal_automations_enabled": True,
            "timezone": "Asia/Dubai",
            "daily_language_lesson_time": "07:30",
        },
        "language": {"target_language": "German", "level": "elementary", "daily_minutes": 7},
    })
    manager = _manager(tmp_path, monkeypatch, profile)

    enabled = manager.set_daily_language_lesson_enabled(True)
    assert enabled["active"] is True
    job = next(item for item in manager.scheduler.list_jobs() if item["prompt"] == "__automation__:daily_language_lesson")
    assert job["repeat"] == {
        "type": "calendar",
        "frequency": "daily",
        "local_time": "07:30",
        "timezone": "Asia/Dubai",
    }
    assert profile.get_profile()["settings"]["daily_language_lesson_enabled"] is True

    disabled = manager.set_daily_language_lesson_enabled(False)
    assert disabled["active"] is False
    assert manager.scheduler.list_jobs()[0]["status"] == "cancelled"
    assert profile.get_profile()["settings"]["daily_language_lesson_enabled"] is False


def test_daily_language_lesson_requires_global_consent_language_and_timezone(tmp_path, monkeypatch):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({
        "settings": {"personal_automations_enabled": True},
        "language": {"target_language": "German"},
    })
    manager = _manager(tmp_path, monkeypatch, profile)

    with pytest.raises(ValueError, match="settings.timezone"):
        manager.set_daily_language_lesson_enabled(True)
    assert manager.scheduler.list_jobs() == []
    assert profile.get_profile()["settings"]["daily_language_lesson_enabled"] is False


def test_daily_language_lesson_uses_local_client_directly_without_failover(tmp_path):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({
        "language": {"target_language": "German", "level": "beginner", "daily_minutes": 5},
    })
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Today's lesson: Guten Morgen."))]
            )

    manager = object.__new__(ConversationManager)
    manager.profile = profile
    manager.llm = SimpleNamespace(
        model="local-test-model",
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    transcript, statuses, speech = [], [], []
    manager._log = lambda _message: None
    manager.on_transcript = transcript.append
    manager.on_status = lambda message, _color=None: statuses.append(message)
    manager.on_speak = speech.append

    result = manager._on_scheduled_job({
        "id": "job-language",
        "execution_id": "exec-language",
        "prompt": "__automation__:daily_language_lesson",
    })

    assert result is True
    assert len(calls) == 1
    assert calls[0]["model"] == "local-test-model"
    assert calls[0]["timeout"] == 90
    assert "tools" not in calls[0]
    assert "German" in calls[0]["messages"][1]["content"]
    assert "Today's lesson" in transcript[0]
    assert statuses and speech


def test_daily_language_lesson_fails_closed_without_local_client(tmp_path):
    profile = UserProfileStore(tmp_path / "profile.json")
    profile.update_profile({"language": {"target_language": "German"}})
    manager = object.__new__(ConversationManager)
    manager.profile = profile
    manager.llm = SimpleNamespace(model="local-test-model", client=None)
    manager._log = lambda _message: None
    manager.on_status = lambda *_args: None

    assert manager._run_daily_language_lesson() is False
