"""Tests for the local daily language lesson prompt builder."""
import pytest

from agent.language_lesson import build_language_lesson_messages


def _profile(target="German", level="beginner", minutes=8):
    return {"language": {"target_language": target, "level": level, "daily_minutes": minutes}}


def test_prompt_uses_saved_language_level_and_time_limit():
    messages = build_language_lesson_messages(_profile("German", "elementary", 7))
    assert [item["role"] for item in messages] == ["system", "user"]
    assert "German" in messages[1]["content"]
    assert "elementary" in messages[1]["content"]
    assert "no more than 7 minutes" in messages[1]["content"]
    assert "Answer key at the end" in messages[1]["content"]


@pytest.mark.parametrize("profile", [
    _profile("", "beginner", 8),
    _profile("German", "expert", 8),
    _profile("German", "beginner", 0),
    _profile("German", "beginner", 11),
    _profile("German", "beginner", True),
])
def test_prompt_rejects_missing_or_invalid_language_preferences(profile):
    with pytest.raises(ValueError):
        build_language_lesson_messages(profile)


def test_prompt_does_not_include_unrelated_personal_profile_sections():
    profile = _profile()
    profile["birthdays"] = [{"label": "Private name", "month_day": "01-02"}]
    profile["shopping"] = {"watchlist": [{"name": "Private product"}]}
    text = " ".join(item["content"] for item in build_language_lesson_messages(profile))
    assert "Private name" not in text
    assert "Private product" not in text
