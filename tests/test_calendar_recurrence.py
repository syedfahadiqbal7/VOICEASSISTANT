"""Calendar recurrence engine validation and DST regression tests."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent.calendar_recurrence import (
    next_calendar_occurrence,
    validate_calendar_recurrence,
)


def test_daily_recurrence_uses_configured_wall_clock_and_timezone():
    rule = {
        "type": "calendar", "frequency": "daily",
        "local_time": "08:15", "timezone": "Asia/Dubai",
    }
    after = datetime(2026, 11, 23, 4, 0, tzinfo=ZoneInfo("UTC"))
    result = next_calendar_occurrence(rule, after)
    assert result.isoformat() == "2026-11-23T08:15:00+04:00"


def test_daily_recurrence_moves_to_next_day_when_today_time_passed():
    rule = {
        "type": "calendar", "frequency": "daily",
        "local_time": "08:15", "timezone": "Asia/Dubai",
    }
    after = datetime(2026, 11, 23, 5, 0, tzinfo=ZoneInfo("UTC"))
    result = next_calendar_occurrence(rule, after)
    assert result.isoformat() == "2026-11-24T08:15:00+04:00"


def test_weekly_recurrence_selects_next_configured_weekday():
    rule = {
        "type": "calendar", "frequency": "weekly",
        "local_time": "09:00", "timezone": "Asia/Dubai",
        "weekdays": [0, 3],
    }
    after = datetime(2026, 11, 24, 8, 0, tzinfo=ZoneInfo("Asia/Dubai"))
    result = next_calendar_occurrence(rule, after)
    assert result.isoformat() == "2026-11-26T09:00:00+04:00"


def test_monthly_recurrence_skips_months_without_requested_day():
    rule = {
        "type": "calendar", "frequency": "monthly",
        "local_time": "10:00", "timezone": "UTC",
        "day_of_month": 31,
    }
    after = datetime(2026, 2, 1, 0, 0, tzinfo=ZoneInfo("UTC"))
    result = next_calendar_occurrence(rule, after)
    assert result.isoformat() == "2026-03-31T10:00:00+00:00"


def test_dst_gap_shifts_forward_to_first_valid_local_minute():
    rule = {
        "type": "calendar", "frequency": "daily",
        "local_time": "02:30", "timezone": "America/New_York",
    }
    after = datetime(2026, 3, 8, 5, 0, tzinfo=ZoneInfo("UTC"))
    result = next_calendar_occurrence(rule, after)
    assert result.isoformat() == "2026-03-08T03:00:00-04:00"


def test_dst_fold_uses_first_occurrence_only():
    rule = {
        "type": "calendar", "frequency": "daily",
        "local_time": "01:30", "timezone": "America/New_York",
    }
    after = datetime(2026, 11, 1, 4, 0, tzinfo=ZoneInfo("UTC"))
    result = next_calendar_occurrence(rule, after)
    assert result.isoformat() == "2026-11-01T01:30:00-04:00"
    assert result.fold == 0


@pytest.mark.parametrize("rule", [
    {"type": "calendar", "frequency": "weekly", "local_time": "08:00",
     "timezone": "UTC", "weekdays": []},
    {"type": "calendar", "frequency": "weekly", "local_time": "08:00",
     "timezone": "UTC", "weekdays": [0, 0]},
    {"type": "calendar", "frequency": "weekly", "local_time": "08:00",
     "timezone": "UTC", "weekdays": [True]},
    {"type": "calendar", "frequency": "monthly", "local_time": "08:00",
     "timezone": "UTC", "day_of_month": 0},
    {"type": "calendar", "frequency": "daily", "local_time": "25:00",
     "timezone": "UTC"},
    {"type": "calendar", "frequency": "daily", "local_time": "08:00",
     "timezone": "Not/AZone"},
    {"type": "calendar", "frequency": "yearly", "local_time": "08:00",
     "timezone": "UTC"},
])
def test_invalid_calendar_recurrence_is_rejected(rule):
    with pytest.raises(ValueError):
        validate_calendar_recurrence(rule)


def test_normalized_weekdays_are_sorted():
    rule = {
        "type": "calendar", "frequency": "weekly",
        "local_time": "08:00", "timezone": "UTC",
        "weekdays": [6, 1],
    }
    assert validate_calendar_recurrence(rule)["weekdays"] == [1, 6]
