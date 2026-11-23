"""Validated, encrypted local profile and preference storage.

This store contains user-selected recommendation preferences only. It never
stores credentials, full birth dates, home addresses, health records or payment
details. Personal context is opt-in and excludes birthday/wardrobe/watchlist data.
"""
from __future__ import annotations

import copy
import math
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import BASE_DIR
from agent.data_protection import DataProtectionError
from agent.redaction import redact_text
from agent.storage import atomic_write_json, load_json

PROFILE_FILE = BASE_DIR / "history" / "user_profile.json"
MAX_LIST_ITEMS = 50
MAX_BIRTHDAYS = 100
MAX_WATCHLIST_ITEMS = 100

DEFAULT_PROFILE: Dict[str, Any] = {
    "version": 1,
    "settings": {
        "assistant_context_enabled": False,
        "personal_automations_enabled": False,
        "timezone": "",
    },
    "interests": [],
    "career": {
        "target_roles": [],
        "preferred_locations": [],
        "industries": [],
        "remote_preference": "any",
    },
    "content": {
        "book_genres": [],
        "movie_genres": [],
        "tv_genres": [],
        "news_topics": [],
        "research_topics": [],
    },
    "language": {
        "target_language": "",
        "level": "beginner",
        "daily_minutes": 10,
    },
    "lifestyle": {
        "cuisines": [],
        "meal_preferences": [],
        "workout_preferences": [],
        "wardrobe_items": [],
    },
    "shopping": {
        "favorite_stores": [],
        "watchlist": [],
    },
    "birthdays": [],
}

_TOP_LEVEL = set(DEFAULT_PROFILE) - {"version"}
_LEVELS = {"beginner", "elementary", "intermediate", "upper_intermediate", "advanced", "fluent"}
_REMOTE = {"any", "remote_only", "hybrid", "on_site"}
_REMINDER_DAYS = {1, 7, 14, 30, 60}
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class UserProfileError(RuntimeError):
    """Profile data is invalid, unavailable or unsafe to update."""


@contextmanager
def _profile_file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    if path.is_symlink() or lock_path.is_symlink():
        raise DataProtectionError("Profile or profile lock path must not be a symbolic link")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                raise RuntimeError("Profile storage requires OS file locking")
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _safe_text(value: Any, field: str, max_length: int = 120) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    if any(ord(ch) < 32 and ch not in "\t" for ch in value):
        raise ValueError(f"{field} contains unsupported control characters")
    if value and redact_text(value) != value:
        raise ValueError(f"{field} appears to contain a secret; remove it before saving")
    return value


def _string_list(value: Any, field: str) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ValueError(f"{field} must be a list of at most {MAX_LIST_ITEMS} items")
    result: List[str] = []
    seen = set()
    for raw in value:
        item = _safe_text(raw, field)
        if not item:
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _only_keys(value: Any, allowed: set[str], field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unknown {field} fields: {', '.join(sorted(unknown))}")
    return value


class UserProfileStore:
    """Thread/process-safe encrypted store for bounded user preferences."""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path or PROFILE_FILE)
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise DataProtectionError("Profile storage path must not be a symbolic link")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._guard():
            self._read_unlocked()

    @contextmanager
    def _guard(self):
        with self._lock:
            with _profile_file_lock(self.path):
                yield

    def _read_unlocked(self) -> Dict[str, Any]:
        raw = load_json(
            self.path,
            default=copy.deepcopy(DEFAULT_PROFILE),
            encrypt=True,
        )
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise UserProfileError("Unsupported or malformed profile document")
        merged = copy.deepcopy(DEFAULT_PROFILE)
        unknown = set(raw) - set(DEFAULT_PROFILE)
        if unknown:
            raise UserProfileError(f"Unknown profile sections: {', '.join(sorted(unknown))}")
        for key, value in raw.items():
            if key == "version":
                continue
            if isinstance(merged.get(key), dict) and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
        try:
            return self._validate(merged)
        except (TypeError, ValueError) as error:
            raise UserProfileError(f"Profile validation failed: {error}") from error

    def _write_unlocked(self, profile: Dict[str, Any]) -> None:
        atomic_write_json(
            self.path,
            self._validate(profile),
            indent=2,
            ensure_ascii=False,
            encrypt=True,
        )

    @staticmethod
    def _validate(profile: Dict[str, Any]) -> Dict[str, Any]:
        data = copy.deepcopy(profile)
        if data.get("version") != 1:
            raise ValueError("profile version must be 1")

        settings = _only_keys(
            data.get("settings"), set(DEFAULT_PROFILE["settings"]), "settings"
        )
        if not isinstance(settings.get("assistant_context_enabled"), bool):
            raise ValueError("assistant_context_enabled must be boolean")
        if not isinstance(settings.get("personal_automations_enabled"), bool):
            raise ValueError("personal_automations_enabled must be boolean")
        timezone_name = _safe_text(settings.get("timezone", ""), "timezone", 80)
        if timezone_name:
            try:
                ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("timezone must be a valid IANA timezone") from error
        data["settings"] = {
            "assistant_context_enabled": settings["assistant_context_enabled"],
            "personal_automations_enabled": settings["personal_automations_enabled"],
            "timezone": timezone_name,
        }

        data["interests"] = _string_list(data.get("interests"), "interests")

        career = _only_keys(data.get("career"), set(DEFAULT_PROFILE["career"]), "career")
        remote = career.get("remote_preference", "any")
        if not isinstance(remote, str) or remote not in _REMOTE:
            raise ValueError("career.remote_preference is invalid")
        data["career"] = {
            "target_roles": _string_list(career.get("target_roles"), "career.target_roles"),
            "preferred_locations": _string_list(career.get("preferred_locations"), "career.preferred_locations"),
            "industries": _string_list(career.get("industries"), "career.industries"),
            "remote_preference": remote,
        }

        content = _only_keys(data.get("content"), set(DEFAULT_PROFILE["content"]), "content")
        data["content"] = {
            key: _string_list(content.get(key), f"content.{key}")
            for key in DEFAULT_PROFILE["content"]
        }

        language = _only_keys(data.get("language"), set(DEFAULT_PROFILE["language"]), "language")
        level = language.get("level", "beginner")
        if not isinstance(level, str) or level not in _LEVELS:
            raise ValueError("language.level is invalid")
        minutes = language.get("daily_minutes", 10)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 10:
            raise ValueError("language.daily_minutes must be an integer from 1 to 10")
        data["language"] = {
            "target_language": _safe_text(language.get("target_language", ""), "language.target_language", 60),
            "level": level,
            "daily_minutes": minutes,
        }

        lifestyle = _only_keys(data.get("lifestyle"), set(DEFAULT_PROFILE["lifestyle"]), "lifestyle")
        data["lifestyle"] = {
            key: _string_list(lifestyle.get(key), f"lifestyle.{key}")
            for key in DEFAULT_PROFILE["lifestyle"]
        }

        shopping = _only_keys(data.get("shopping"), set(DEFAULT_PROFILE["shopping"]), "shopping")
        stores = _string_list(shopping.get("favorite_stores"), "shopping.favorite_stores")
        raw_watchlist = shopping.get("watchlist")
        if not isinstance(raw_watchlist, list) or len(raw_watchlist) > MAX_WATCHLIST_ITEMS:
            raise ValueError(f"shopping.watchlist must contain at most {MAX_WATCHLIST_ITEMS} items")
        watchlist = []
        seen_watchlist = set()
        for item in raw_watchlist:
            item = _only_keys(item, {"id", "name", "category", "target_price", "currency"}, "watchlist item")
            name = _safe_text(item.get("name"), "watchlist.name")
            if not name:
                raise ValueError("watchlist.name cannot be empty")
            identity = name.casefold()
            if identity in seen_watchlist:
                continue
            seen_watchlist.add(identity)
            price = item.get("target_price")
            if price is not None:
                if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or price <= 0:
                    raise ValueError("watchlist.target_price must be a positive finite number or null")
                price = float(price)
            currency = _safe_text(item.get("currency", "USD"), "watchlist.currency", 3).upper()
            if not _CURRENCY.fullmatch(currency):
                raise ValueError("watchlist.currency must be a three-letter currency code")
            item_id = _safe_text(item.get("id") or str(uuid.uuid4()), "watchlist.id", 64)
            watchlist.append({
                "id": item_id,
                "name": name,
                "category": _safe_text(item.get("category", ""), "watchlist.category"),
                "target_price": price,
                "currency": currency,
            })
        data["shopping"] = {"favorite_stores": stores, "watchlist": watchlist}

        raw_birthdays = data.get("birthdays")
        if not isinstance(raw_birthdays, list) or len(raw_birthdays) > MAX_BIRTHDAYS:
            raise ValueError(f"birthdays must contain at most {MAX_BIRTHDAYS} entries")
        birthdays = []
        seen_birthdays = set()
        for item in raw_birthdays:
            item = _only_keys(item, {"id", "label", "month_day", "reminder_days"}, "birthday")
            label = _safe_text(item.get("label"), "birthday.label")
            month_day = _safe_text(item.get("month_day"), "birthday.month_day", 5)
            try:
                month, day = (int(part) for part in month_day.split("-"))
                date(2000, month, day)
            except (ValueError, TypeError):
                raise ValueError("birthday.month_day must be MM-DD (year is not stored)")
            identity = (label.casefold(), month_day)
            if identity in seen_birthdays:
                continue
            seen_birthdays.add(identity)
            reminder_days = item.get("reminder_days", [30, 7, 1])
            if (
                not isinstance(reminder_days, list)
                or any(isinstance(v, bool) or not isinstance(v, int) or v not in _REMINDER_DAYS for v in reminder_days)
            ):
                raise ValueError("birthday.reminder_days may contain only 1, 7, 14, 30 or 60")
            birthdays.append({
                "id": _safe_text(item.get("id") or str(uuid.uuid4()), "birthday.id", 64),
                "label": label,
                "month_day": month_day,
                "reminder_days": sorted(set(reminder_days), reverse=True),
            })
        data["birthdays"] = birthdays
        return data

    def get_profile(self) -> Dict[str, Any]:
        with self._guard():
            return copy.deepcopy(self._read_unlocked())

    def update_profile(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(updates, dict):
            raise ValueError("profile updates must be an object")
        with self._guard():
            current = self._read_unlocked()
            unknown = set(updates) - _TOP_LEVEL
            if unknown:
                raise ValueError(f"Unknown profile sections: {', '.join(sorted(unknown))}")
            candidate = copy.deepcopy(current)
            for key, value in updates.items():
                if isinstance(candidate.get(key), dict) and isinstance(value, dict):
                    candidate[key].update(value)
                else:
                    candidate[key] = value
            candidate = self._validate(candidate)
            self._write_unlocked(candidate)
            return copy.deepcopy(candidate)

    def add_birthday(
        self, label: str, month_day: str, reminder_days: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        with self._guard():
            profile = self._read_unlocked()
            normalized_label = _safe_text(label, "birthday.label")
            normalized_day = _safe_text(month_day, "birthday.month_day", 5)
            existing = next((
                item for item in profile["birthdays"]
                if item["label"].casefold() == normalized_label.casefold()
                and item["month_day"] == normalized_day
            ), None)
            if existing:
                return copy.deepcopy(existing)
            item = {
                "id": str(uuid.uuid4()),
                "label": normalized_label,
                "month_day": normalized_day,
                "reminder_days": reminder_days if reminder_days is not None else [30, 7, 1],
            }
            profile["birthdays"].append(item)
            profile = self._validate(profile)
            self._write_unlocked(profile)
            return copy.deepcopy(next(x for x in profile["birthdays"] if x["id"] == item["id"]))

    def remove_birthday(self, birthday_id: str) -> bool:
        with self._guard():
            profile = self._read_unlocked()
            remaining = [item for item in profile["birthdays"] if item["id"] != birthday_id]
            if len(remaining) == len(profile["birthdays"]):
                return False
            profile["birthdays"] = remaining
            self._write_unlocked(profile)
            return True

    def add_watchlist_item(
        self, name: str, category: str = "", target_price: Optional[float] = None, currency: str = "USD"
    ) -> Dict[str, Any]:
        with self._guard():
            profile = self._read_unlocked()
            normalized_name = _safe_text(name, "watchlist.name")
            existing = next((
                item for item in profile["shopping"]["watchlist"]
                if item["name"].casefold() == normalized_name.casefold()
            ), None)
            if existing:
                return copy.deepcopy(existing)
            item = {
                "id": str(uuid.uuid4()),
                "name": normalized_name,
                "category": category,
                "target_price": target_price,
                "currency": currency,
            }
            profile["shopping"]["watchlist"].append(item)
            profile = self._validate(profile)
            self._write_unlocked(profile)
            return copy.deepcopy(next(x for x in profile["shopping"]["watchlist"] if x["id"] == item["id"]))

    def remove_watchlist_item(self, item_id: str) -> bool:
        with self._guard():
            profile = self._read_unlocked()
            remaining = [item for item in profile["shopping"]["watchlist"] if item["id"] != item_id]
            if len(remaining) == len(profile["shopping"]["watchlist"]):
                return False
            profile["shopping"]["watchlist"] = remaining
            self._write_unlocked(profile)
            return True

    def context_snippet(self, max_chars: int = 600) -> str:
        profile = self.get_profile()
        if not profile["settings"]["assistant_context_enabled"]:
            return ""
        lines = []
        if profile["interests"]:
            lines.append("Interests: " + ", ".join(profile["interests"][:8]))
        career = profile["career"]
        if career["target_roles"]:
            lines.append("Career targets: " + ", ".join(career["target_roles"][:5]))
        if career["industries"]:
            lines.append("Industries: " + ", ".join(career["industries"][:5]))
        if profile["content"]["news_topics"]:
            lines.append("News topics: " + ", ".join(profile["content"]["news_topics"][:8]))
        language = profile["language"]
        if language["target_language"]:
            lines.append(f"Language learning: {language['target_language']} ({language['level']})")
        return "\n".join(lines)[:max(0, min(int(max_chars), 2000))]
