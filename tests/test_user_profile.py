"""Validation, privacy and concurrency tests for UserProfileStore."""
from __future__ import annotations

import threading

import pytest

from agent.user_profile import UserProfileError, UserProfileStore
from agent.storage import load_json


def test_profile_defaults_are_private_and_context_is_opt_in(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    profile = store.get_profile()
    assert profile["settings"]["assistant_context_enabled"] is False
    assert profile["settings"]["personal_automations_enabled"] is False
    assert store.context_snippet() == ""

    store.update_profile({
        "interests": ["AI", "Banking"],
        "career": {"target_roles": ["Solution Architect"]},
        "settings": {"assistant_context_enabled": True},
        "birthdays": [{
            "id": "b1", "label": "Family member", "month_day": "04-12",
            "reminder_days": [30, 7, 1],
        }],
    })
    context = store.context_snippet()
    assert "AI" in context
    assert "Solution Architect" in context
    assert "Family member" not in context
    assert "04-12" not in context


def test_profile_is_encrypted_and_round_trips(tmp_path):
    path = tmp_path / "profile.json"
    store = UserProfileStore(path)
    store.update_profile({"interests": ["Private interest"]})
    raw = path.read_bytes()
    assert b"Private interest" not in raw
    assert UserProfileStore(path).get_profile()["interests"] == ["Private interest"]


def test_profile_rejects_unknown_sections_and_fields(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises(ValueError, match="Unknown profile sections"):
        store.update_profile({"unknown": []})
    with pytest.raises(UserProfileError, match="Profile validation failed"):
        store.update_profile({"career": {"admin": True}})


@pytest.mark.parametrize("settings", [
    {"timezone": "Not/AZone"},
    {"assistant_context_enabled": "yes"},
])
def test_profile_rejects_invalid_settings(tmp_path, settings):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises((ValueError, UserProfileError)):
        store.update_profile({"settings": settings})


@pytest.mark.parametrize("language", [
    {"level": "expert"},
    {"daily_minutes": 0},
    {"daily_minutes": 11},
    {"daily_minutes": True},
])
def test_profile_rejects_invalid_language_settings(tmp_path, language):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises((ValueError, UserProfileError)):
        store.update_profile({"language": language})


def test_profile_rejects_secret_like_text(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises((ValueError, UserProfileError), match="secret"):
        store.update_profile({"interests": ["token=super-secret-value"]})


def test_birthday_stores_only_month_and_day_and_supports_reminders(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    item = store.add_birthday("Family member", "02-29", [30, 7, 1])
    assert item["month_day"] == "02-29"
    assert item["reminder_days"] == [30, 7, 1]
    assert store.remove_birthday(item["id"]) is True
    assert store.remove_birthday(item["id"]) is False


@pytest.mark.parametrize("month_day", ["2020-02-29", "02-30", "13-01", "2-1", ""])
def test_birthday_rejects_full_year_or_invalid_dates(tmp_path, month_day):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises((ValueError, UserProfileError)):
        store.add_birthday("Test", month_day)


def test_watchlist_validates_price_currency_and_deduplicates(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    item = store.add_watchlist_item("Noise cancelling headphones", "Electronics", 250, "AED")
    duplicate = store.add_watchlist_item("noise cancelling headphones", "Audio", 200, "AED")
    assert item["id"] == duplicate["id"]
    assert len(store.get_profile()["shopping"]["watchlist"]) == 1
    assert store.remove_watchlist_item(item["id"]) is True


@pytest.mark.parametrize("price", [0, -1, True, float("nan"), float("inf")])
def test_watchlist_rejects_invalid_target_price(tmp_path, price):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises((ValueError, UserProfileError)):
        store.add_watchlist_item("Product", target_price=price)


def test_profile_rejects_invalid_currency_and_bounded_lists(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    with pytest.raises((ValueError, UserProfileError)):
        store.add_watchlist_item("Product", target_price=10, currency="US")
    with pytest.raises((ValueError, UserProfileError)):
        store.update_profile({"interests": [f"topic-{i}" for i in range(51)]})


def test_profile_updates_from_multiple_instances_do_not_lose_sections(tmp_path):
    path = tmp_path / "profile.json"
    first = UserProfileStore(path)
    second = UserProfileStore(path)
    barrier = threading.Barrier(2)

    def update(store, section, value):
        barrier.wait()
        store.update_profile({section: value})

    threads = [
        threading.Thread(target=update, args=(first, "interests", ["AI"])),
        threading.Thread(target=update, args=(second, "content", {"news_topics": ["Technology"]})),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    profile = UserProfileStore(path).get_profile()
    assert profile["interests"] == ["AI"]
    assert profile["content"]["news_topics"] == ["Technology"]


def test_corrupt_profile_fails_closed_without_resetting_file(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"version":99}', encoding="utf-8")
    with pytest.raises(UserProfileError):
        UserProfileStore(path)
    # Legacy plaintext is encrypted on read, but malformed data is preserved;
    # the store must never silently replace it with an empty profile.
    assert load_json(path, encrypt=True) == {"version": 99}

def test_profile_editor_updates_preserve_birthday_and_watchlist_ids(tmp_path):
    store = UserProfileStore(tmp_path / "profile.json")
    birthday = store.add_birthday("Family member", "04-12")
    product = store.add_watchlist_item("Headphones", "Audio", 100, "AED")
    store.update_profile({
        "birthdays": [{"label": "Family member", "month_day": "04-12", "reminder_days": [30, 7, 1]}],
        "shopping": {
            "favorite_stores": [],
            "watchlist": [{"name": "Headphones", "category": "Audio", "target_price": 90, "currency": "AED"}],
        },
    })
    profile = store.get_profile()
    assert profile["birthdays"][0]["id"] == birthday["id"]
    assert profile["shopping"]["watchlist"][0]["id"] == product["id"]
    assert profile["shopping"]["watchlist"][0]["target_price"] == 90.0

def test_conversation_manager_refreshes_only_opted_in_profile_context(tmp_path):
    from types import SimpleNamespace
    from agent.conversation_manager import ConversationManager

    store = UserProfileStore(tmp_path / "profile.json")
    store.update_profile({"interests": ["Technology"], "settings": {"assistant_context_enabled": True}})
    manager = object.__new__(ConversationManager)
    manager.memory = SimpleNamespace(context_snippet=lambda: "")
    manager.profile = store
    manager.session_messages = [{"role": "system", "content": "old"}]
    manager.refresh_profile_context()
    assert "Technology" in manager.session_messages[0]["content"]
    assert "[User Preferences]" in manager.session_messages[0]["content"]
