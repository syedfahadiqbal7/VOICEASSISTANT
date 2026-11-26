"""Declarative personal automation catalog and readiness tests."""
from __future__ import annotations

import pytest

from agent.automation_catalog import (
    AUTOMATION_CATALOG,
    AUTOMATION_BY_ID,
    automation_readiness,
    get_automation,
    list_automations,
)


def _profile():
    return {
        "settings": {"timezone": "Asia/Dubai"},
        "career": {
            "industries": ["Technology"],
            "target_roles": ["Solution Architect"],
            "preferred_locations": ["UAE"],
        },
        "content": {
            "book_genres": ["Technology"],
            "movie_genres": ["Drama"],
            "tv_genres": ["Documentary"],
            "news_topics": ["AI"],
            "research_topics": ["LLMs"],
        },
        "language": {
            "target_language": "German",
            "level": "beginner",
            "daily_minutes": 8,
        },
        "lifestyle": {
            "wardrobe_items": ["Business casual"],
            "cuisines": ["Pakistani"],
            "meal_preferences": ["Halal"],
            "workout_preferences": ["Walking"],
        },
        "shopping": {
            "favorite_stores": ["Store A"],
            "watchlist": [{"name": "Laptop"}],
        },
        "interests": ["Technology"],
        "birthdays": [{"label": "Family", "month_day": "12-04"}],
    }


def test_catalog_has_unique_stable_ids_and_all_entries_start_disabled():
    ids = [item.id for item in AUTOMATION_CATALOG]
    assert len(ids) == 34
    assert len(ids) == len(set(ids))
    assert set(ids) == set(AUTOMATION_BY_ID)
    assert all(item.default_enabled is False for item in AUTOMATION_CATALOG)
    assert all(item.title and item.description and item.category and item.cadence for item in AUTOMATION_CATALOG)


def test_catalog_lookup_and_category_filter_are_stable():
    assert get_automation("job_search_mon_thu").weekdays == (0, 3)
    assert get_automation("saturday_home_task").weekdays == (5,)
    assert get_automation("sunday_family_tasks").weekdays == (6,)
    assert len(list_automations("Career")) == 3
    assert get_automation("unknown") is None


def test_readiness_fails_closed_when_global_switch_is_off():
    result = automation_readiness(
        "daily_language_lesson",
        profile=_profile(),
        connected_integrations=(),
        registered_handlers={"daily_language_lesson"},
        master_enabled=False,
    )
    assert result["ready"] is False
    assert result["can_enable"] is False
    assert "personal automations are globally disabled" in result["blockers"]


def test_readiness_requires_registered_handler_and_profile_fields():
    result = automation_readiness(
        "daily_weather_outfit",
        profile={"settings": {"timezone": ""}, "lifestyle": {"wardrobe_items": []}},
        connected_integrations={"weather"},
        registered_handlers=(),
        master_enabled=True,
    )
    assert result["ready"] is False
    assert "automation handler is not registered" in result["blockers"]
    assert "settings.timezone" in result["missing_profile_fields"]
    assert "lifestyle.wardrobe_items" in result["missing_profile_fields"]


def test_readiness_requires_every_required_integration():
    result = automation_readiness(
        "weekday_work_email_briefing",
        profile=_profile(),
        connected_integrations=(),
        registered_handlers={"weekday_work_email_briefing"},
        master_enabled=True,
    )
    assert result["ready"] is False
    assert result["missing_integrations"] == ["email"]


def test_any_of_integration_group_accepts_one_connected_provider():
    result = automation_readiness(
        "weekly_deadlines_prep",
        profile=_profile(),
        connected_integrations={"EMAIL"},
        registered_handlers={"weekly_deadlines_prep"},
        master_enabled=True,
    )
    assert result["ready"] is True
    assert result["can_enable"] is True
    assert result["blockers"] == []


def test_any_of_integration_group_does_not_accept_unrelated_connector():
    result = automation_readiness(
        "weekly_deadlines_prep",
        profile=_profile(),
        connected_integrations={"weather"},
        registered_handlers={"weekly_deadlines_prep"},
        master_enabled=True,
    )
    assert result["ready"] is False
    assert result["missing_integration_groups"] == [["calendar", "email"]]


def test_readiness_rejects_unknown_automation():
    with pytest.raises(KeyError):
        automation_readiness(
            "not_a_real_automation",
            profile=_profile(),
            master_enabled=True,
        )

def test_inbox_delivery_return_and_renewal_checks_are_separate_workflows():
    delivery = get_automation("daily_package_tracking")
    returns = get_automation("return_deadline_reminders")
    renewals = get_automation("renewal_reminders")
    assert delivery.required_integrations == ("email", "carrier_tracking")
    assert returns.required_integrations == ("email",)
    assert renewals.required_integrations == ("email",)
    family = get_automation("sunday_family_tasks")
    assert "deadlines, replies" in family.description

def test_desktop_catalog_cadence_labels_match_schedule_rules():
    from ui.automation_catalog_viewer import AutomationCatalogViewer

    assert AutomationCatalogViewer._cadence(get_automation("job_search_mon_thu")) == "Weekly (Mon, Thu)"
    assert AutomationCatalogViewer._cadence(get_automation("sunday_family_tasks")) == "Weekly (Sun)"
    assert AutomationCatalogViewer._cadence(get_automation("birthday_gift_planning")) == "Event-driven (30 days before)"
    requirements = AutomationCatalogViewer._requirements(get_automation("daily_package_tracking"))
    assert "email" in requirements and "carrier_tracking" in requirements

def test_birthday_reminders_require_timezone_and_a_real_registered_handler():
    profile = _profile()
    result = automation_readiness(
        "birthday_reminders",
        profile=profile,
        connected_integrations=(),
        registered_handlers={"birthday_reminders"},
        master_enabled=True,
    )
    assert result["can_enable"] is True
    profile["settings"]["timezone"] = ""
    blocked = automation_readiness(
        "birthday_reminders",
        profile=profile,
        connected_integrations=(),
        registered_handlers={"birthday_reminders"},
        master_enabled=True,
    )
    assert blocked["can_enable"] is False
    assert "settings.timezone" in blocked["missing_profile_fields"]

def test_daily_language_lesson_readiness_requires_local_timezone_and_profile():
    profile = _profile()
    result = automation_readiness(
        "daily_language_lesson",
        profile=profile,
        connected_integrations=(),
        registered_handlers={"daily_language_lesson"},
        master_enabled=True,
    )
    assert result["can_enable"] is True
    profile["settings"]["timezone"] = ""
    blocked = automation_readiness(
        "daily_language_lesson",
        profile=profile,
        connected_integrations=(),
        registered_handlers={"daily_language_lesson"},
        master_enabled=True,
    )
    assert blocked["can_enable"] is False
    assert "settings.timezone" in blocked["missing_profile_fields"]
