"""Deterministic, local birthday reminder calculations.

Only month/day is read from the encrypted profile. No birth year or external
account is required. Leap-day birthdays are observed on February 29 only in
leap years; otherwise the next valid occurrence is used.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping
from zoneinfo import ZoneInfo


def _next_birthday(today: date, month_day: str) -> date:
    try:
        month, day = (int(part) for part in month_day.split("-", 1))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Birthday must use MM-DD") from error
    for year in range(today.year, today.year + 9):
        try:
            candidate = date(year, month, day)
        except ValueError:
            # February 29 is skipped in non-leap years; other invalid dates fail.
            if (month, day) == (2, 29):
                continue
            raise ValueError("Birthday month/day is invalid")
        if candidate >= today:
            return candidate
    raise ValueError("Could not resolve next birthday")


def due_birthday_reminders(
    profile: Mapping[str, Any],
    now: datetime | None = None,
) -> List[str]:
    """Return reminders due today in the profile's IANA timezone."""
    settings = profile.get("settings", {})
    timezone_name = settings.get("timezone") if isinstance(settings, Mapping) else None
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ValueError("Set an IANA timezone in Profile before enabling birthday reminders")
    try:
        zone = ZoneInfo(timezone_name)
    except Exception as error:
        raise ValueError("Profile timezone is not available") from error
    local_now = (now or datetime.now(zone)).astimezone(zone)
    today = local_now.date()
    birthdays = profile.get("birthdays", [])
    if not isinstance(birthdays, list):
        raise ValueError("Profile birthdays must be a list")

    reminders: List[str] = []
    for item in birthdays:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("label") or "Someone special").strip()[:80]
        occurrence = _next_birthday(today, str(item.get("month_day") or ""))
        days_until = (occurrence - today).days
        raw_days = item.get("reminder_days", [])
        if not isinstance(raw_days, list):
            continue
        if days_until in raw_days:
            suffix = "tomorrow" if days_until == 1 else f"in {days_until} days"
            reminders.append(f"{label}'s birthday is {suffix} ({occurrence.strftime('%d %b')}).")
    return reminders
