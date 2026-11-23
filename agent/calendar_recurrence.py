"""Timezone-aware calendar recurrence validation and occurrence calculation.

Calendar jobs use wall-clock time in an IANA timezone. Non-existent local
times during a DST spring-forward gap shift to the first valid local minute.
Repeated times during a fall-back fold use the earlier occurrence (fold=0).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_calendar_recurrence(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Calendar recurrence must be an object")
    frequency = value.get("frequency")
    required = {"type", "frequency", "local_time", "timezone"}
    allowed = required | {"weekdays", "day_of_month"}
    if set(value) - allowed or not required.issubset(value):
        raise ValueError("Calendar recurrence has missing or unsupported fields")
    if value.get("type") != "calendar":
        raise ValueError("Recurrence type must be calendar")
    if not isinstance(frequency, str) or frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("Frequency must be daily, weekly or monthly")
    local_time = value.get("local_time")
    if not isinstance(local_time, str) or len(local_time) != 5 or local_time[2] != ":":
        raise ValueError("local_time must use HH:MM")
    try:
        hour, minute = (int(part) for part in local_time.split(":"))
        time(hour, minute)
    except (TypeError, ValueError):
        raise ValueError("local_time must be a valid 24-hour HH:MM value")
    timezone_name = value.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("timezone must be an IANA timezone name")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("timezone is not a recognized IANA timezone")
    if frequency == "weekly":
        weekdays = value.get("weekdays")
        if (not isinstance(weekdays, list) or not weekdays
                or any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6
                       for day in weekdays)
                or len(set(weekdays)) != len(weekdays)):
            raise ValueError("weekly recurrence requires unique weekdays from 0 (Monday) to 6 (Sunday)")
    elif "weekdays" in value:
        raise ValueError("weekdays is only valid for weekly recurrence")
    if frequency == "monthly":
        day = value.get("day_of_month")
        if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
            raise ValueError("monthly recurrence requires day_of_month from 1 to 31")
    elif "day_of_month" in value:
        raise ValueError("day_of_month is only valid for monthly recurrence")
    normalized = {
        "type": "calendar",
        "frequency": frequency,
        "local_time": f"{hour:02d}:{minute:02d}",
        "timezone": timezone_name,
    }
    if frequency == "weekly":
        normalized["weekdays"] = sorted(value["weekdays"])
    if frequency == "monthly":
        normalized["day_of_month"] = value["day_of_month"]
    return normalized


def _resolve_wall_time(day: date, clock: time, zone: ZoneInfo) -> datetime:
    naive = datetime.combine(day, clock)
    # Round-trip through UTC detects nonexistent local wall times. fold=0
    # deterministically selects the earlier instant when a time repeats.
    utc = ZoneInfo("UTC")
    for offset in range(181):
        candidate_naive = naive + timedelta(minutes=offset)
        candidate = candidate_naive.replace(tzinfo=zone, fold=0)
        round_trip = candidate.astimezone(utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == candidate_naive:
            return candidate
    raise ValueError("Could not resolve local time around timezone transition")


def next_calendar_occurrence(recurrence: Any, after: datetime) -> datetime:
    """Return the first scheduled instant strictly after the reference instant."""
    rule = validate_calendar_recurrence(recurrence)
    zone = ZoneInfo(rule["timezone"])
    utc = ZoneInfo("UTC")
    if after.tzinfo is None:
        after = after.astimezone()
    after_utc = after.astimezone(utc)
    local_after = after_utc.astimezone(zone)
    start = local_after.date()
    hour, minute = (int(part) for part in rule["local_time"].split(":"))
    clock = time(hour, minute)

    for delta in range(0, 367 * 5):
        day = start + timedelta(days=delta)
        frequency = rule["frequency"]
        if frequency == "weekly" and day.weekday() not in rule["weekdays"]:
            continue
        if frequency == "monthly" and day.day != rule["day_of_month"]:
            continue
        candidate = _resolve_wall_time(day, clock, zone)
        if candidate.astimezone(utc) > after_utc:
            return candidate
    raise ValueError("No calendar occurrence found within the supported search horizon")
