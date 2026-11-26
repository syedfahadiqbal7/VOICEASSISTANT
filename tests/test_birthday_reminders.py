"""Tests for local, privacy-preserving birthday reminder calculations."""
from datetime import date, datetime, timezone

import pytest

from agent.birthday_reminders import due_birthday_reminders


def _profile(birthdays, timezone_name="Asia/Dubai"):
    return {
        "settings": {"timezone": timezone_name},
        "birthdays": birthdays,
    }


def test_reminder_due_on_configured_day_before_birthday():
    profile = _profile([
        {"label": "Family member", "month_day": "11-30", "reminder_days": [30, 7, 1]},
    ])
    now = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
    assert due_birthday_reminders(profile, now) == [
        "Family member's birthday is in 7 days (30 Nov)."
    ]


def test_reminder_due_tomorrow():
    profile = _profile([
        {"label": "Friend", "month_day": "11-27", "reminder_days": [1]},
    ])
    now = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)
    assert due_birthday_reminders(profile, now) == [
        "Friend's birthday is tomorrow (27 Nov)."
    ]


def test_no_reminder_when_no_configured_day_matches():
    profile = _profile([
        {"label": "Friend", "month_day": "11-27", "reminder_days": [7]},
    ])
    now = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)
    assert due_birthday_reminders(profile, now) == []


def test_timezone_conversion_uses_profile_local_date():
    profile = _profile([
        {"label": "Friend", "month_day": "11-27", "reminder_days": [1]},
    ], "Asia/Dubai")
    # 20:30 UTC is already 00:30 on Nov 27 in Dubai.
    now = datetime(2026, 11, 26, 20, 30, tzinfo=timezone.utc)
    assert due_birthday_reminders(profile, now) == []


def test_year_rollover_uses_next_calendar_year():
    profile = _profile([
        {"label": "Friend", "month_day": "01-02", "reminder_days": [7]},
    ])
    now = datetime(2026, 12, 26, 9, 0, tzinfo=timezone.utc)
    assert due_birthday_reminders(profile, now) == [
        "Friend's birthday is in 7 days (02 Jan)."
    ]


def test_february_29_is_only_observed_in_leap_years():
    profile = _profile([
        {"label": "Leap day", "month_day": "02-29", "reminder_days": [1]},
    ])
    now = datetime(2027, 2, 28, 9, 0, tzinfo=timezone.utc)
    assert due_birthday_reminders(profile, now) == []


def test_timezone_is_required_and_validated():
    with pytest.raises(ValueError, match="Set an IANA timezone"):
        due_birthday_reminders(_profile([], ""))
    with pytest.raises(ValueError, match="Profile timezone"):
        due_birthday_reminders(_profile([], "Not/AZone"))


def test_birth_year_is_not_needed_or_included_in_reminder():
    profile = _profile([
        {"label": "Relative", "month_day": "12-01", "reminder_days": [1]},
    ])
    now = datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)
    message = due_birthday_reminders(profile, now)[0]
    assert "12-01" not in message
    assert "2026" not in message
    assert "Relative" in message
