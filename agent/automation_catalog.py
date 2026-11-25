"""Catalog and readiness rules for the user's recurring personal automations.

Definitions are declarative only: they do not imply that an integration is
connected or that a workflow is currently executable. Every automation starts
disabled and must have a real handler, required profile data and integrations
before it can be enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


@dataclass(frozen=True)
class AutomationDefinition:
    id: str
    title: str
    category: str
    description: str
    cadence: str
    weekdays: Tuple[int, ...] = ()
    days_before: Optional[int] = None
    required_profile_fields: Tuple[str, ...] = ()
    required_integrations: Tuple[str, ...] = ()
    any_integration_groups: Tuple[Tuple[str, ...], ...] = ()
    approval_required: bool = False
    default_enabled: bool = False


# Weekday numbering follows datetime.weekday(): Monday=0 ... Sunday=6.
# A blank time is intentional: users choose their own local delivery time.
_AUTOMATIONS = (
    AutomationDefinition("industry_news_weekly", "Track industry news", "News & research", "Weekly roundup of relevant industry news.", "weekly", required_profile_fields=("career.industries",), required_integrations=("web_research",)),
    AutomationDefinition("book_recommendations_monthly", "Recommend three books", "Learning & entertainment", "Recommend three books each month using saved reading interests and genres.", "monthly", required_profile_fields=("content.book_genres",), required_integrations=("web_research",)),
    AutomationDefinition("daily_language_lesson", "Daily language lesson", "Learning & entertainment", "Deliver a level-appropriate lesson that takes under ten minutes.", "daily", required_profile_fields=("language.target_language", "language.level", "language.daily_minutes")),
    AutomationDefinition("saturday_home_task", "Saturday home task", "Home & family", "Suggest one manageable home task every Saturday.", "weekly", weekdays=(5,)),
    AutomationDefinition("monthly_budget_template", "Monthly blank budget", "Money & shopping", "Prepare a fresh blank monthly budget template without accessing bank accounts.", "monthly"),
    AutomationDefinition("job_search_mon_thu", "Job matches Monday and Thursday", "Career", "Find fresh roles and explain fit, location and how to apply.", "weekly", weekdays=(0, 3), required_profile_fields=("career.target_roles", "career.preferred_locations"), required_integrations=("job_sources",)),
    AutomationDefinition("friday_movies_shows", "Friday movie and show picks", "Learning & entertainment", "Recommend three movies or shows for Friday night.", "weekly", weekdays=(4,), required_profile_fields=("content.movie_genres", "content.tv_genres"), required_integrations=("web_research",)),
    AutomationDefinition("weekly_store_coupons", "Weekly store coupons", "Money & shopping", "Find current coupons for saved favorite stores and report restrictions.", "weekly", required_profile_fields=("shopping.favorite_stores",), required_integrations=("web_research",)),
    AutomationDefinition("daily_weather_outfit", "Weather-based outfit", "Lifestyle", "Suggest an outfit using saved wardrobe items and the local weather.", "daily", required_profile_fields=("lifestyle.wardrobe_items", "settings.timezone"), required_integrations=("weather",)),
    AutomationDefinition("thursday_weekend_ideas", "Thursday weekend ideas", "Local discovery", "Find relevant activities and places for the coming weekend.", "weekly", weekdays=(3,), required_integrations=("local_discovery",)),
    AutomationDefinition("daily_news_roundup", "Daily news roundup", "News & research", "Summarize important daily news using the user's selected topics.", "daily", required_profile_fields=("content.news_topics",), required_integrations=("news_sources",)),
    AutomationDefinition("weekly_concerts", "Weekly concert discovery", "Local discovery", "Find newly announced concerts in preferred locations.", "weekly", required_profile_fields=("career.preferred_locations",), required_integrations=("event_sources",)),
    AutomationDefinition("weekly_dinners_grocery", "Five weeknight dinners and grocery list", "Lifestyle", "Plan five weeknight dinners and produce one consolidated grocery list.", "weekly", required_profile_fields=("lifestyle.cuisines", "lifestyle.meal_preferences")),
    AutomationDefinition("saturday_article", "Saturday article", "Learning & entertainment", "Select one useful article for Saturday based on saved interests.", "weekly", weekdays=(5,), required_profile_fields=("interests",), required_integrations=("web_research",)),
    AutomationDefinition("topic_change_alerts", "Important topic change alerts", "News & research", "Monitor selected topics and alert only on material changes.", "event", required_profile_fields=("content.news_topics",), required_integrations=("news_sources",)),
    AutomationDefinition("weekly_product_watchlist", "Weekly product shortlist changes", "Money & shopping", "Report meaningful changes to saved product shortlists.", "weekly", required_profile_fields=("shopping.watchlist",), required_integrations=("price_sources",)),
    AutomationDefinition("weekly_workout_plan", "Weekly workout plan", "Lifestyle", "Prepare a weekly workout plan from user-selected fitness preferences.", "weekly", required_profile_fields=("lifestyle.workout_preferences",)),
    AutomationDefinition("friday_research_roundup", "Friday research roundup", "News & research", "Summarize new research in the user's selected topics.", "weekly", weekdays=(4,), required_profile_fields=("content.research_topics",), required_integrations=("research_sources",)),
    AutomationDefinition("daily_three_day_forecast", "Daily three-day forecast", "Lifestyle", "Deliver the local three-day weather forecast each morning.", "daily", required_profile_fields=("settings.timezone",), required_integrations=("weather",)),
    AutomationDefinition("birthday_gift_planning", "Birthday gift planning", "Home & family", "Start gift research ahead of saved birthdays.", "event", required_profile_fields=("birthdays",), days_before=30, required_integrations=("web_research",)),
    AutomationDefinition("daily_price_alerts", "Daily price alerts", "Money & shopping", "Check saved price targets and report qualifying price changes.", "event", required_profile_fields=("shopping.watchlist",), required_integrations=("price_sources",)),
    AutomationDefinition("birthday_reminders", "Birthday reminders", "Home & family", "Send reminders using each saved birthday's selected reminder days.", "event", required_profile_fields=("birthdays",)),
    AutomationDefinition("weekly_email_declutter", "Weekly email declutter", "Email & productivity", "Summarize low-priority mail and propose safe cleanup actions; deletion requires approval.", "weekly", required_integrations=("email",), approval_required=True),
    AutomationDefinition("free_trial_expiry_reminders", "Free-trial expiry reminders", "Money & shopping", "Remind two days before a detected free-trial expiry.", "event", days_before=2, required_integrations=("email",)),
    AutomationDefinition("weekday_work_email_briefing", "Weekday urgent email briefing", "Email & productivity", "Summarize urgent work email, replies owed and suggested next steps.", "weekly", weekdays=(0, 1, 2, 3, 4), required_integrations=("email",)),
    AutomationDefinition("daily_company_news", "Daily company news", "Career", "Track important news for companies selected by the user.", "daily", required_integrations=("news_sources",)),
    AutomationDefinition("competitor_monitoring", "Competitor monitoring", "Business intelligence", "Monitor selected competitors for material product, pricing and company changes.", "weekly", required_integrations=("web_research",)),
    AutomationDefinition("weekly_application_updates", "Weekly job application updates", "Career", "Review saved applications and report status changes and follow-ups.", "weekly", required_integrations=("job_tracker",)),
    AutomationDefinition("weekly_deadlines_prep", "Weekly deadlines and preparation", "Email & productivity", "Review upcoming deadlines and summarize preparation needed.", "weekly", any_integration_groups=(("calendar", "email"),)),
    AutomationDefinition("daily_package_tracking", "Daily package delivery updates", "Email & productivity", "Check the inbox daily for package delivery updates and shipment status changes.", "daily", required_integrations=("email", "carrier_tracking")),
    AutomationDefinition("return_deadline_reminders", "Return deadline reminders", "Email & productivity", "Check the inbox daily for return deadlines and remind before the return window closes.", "daily", required_integrations=("email",)),
    AutomationDefinition("renewal_reminders", "Renewal reminders", "Email & productivity", "Check the inbox daily for upcoming renewals and surface the renewal date and terms.", "daily", required_integrations=("email",)),
    AutomationDefinition("sunday_family_tasks", "Sunday family to-do list", "Home & family", "Gather deadlines, replies and family to-dos into one list every Sunday.", "weekly", weekdays=(6,), required_integrations=("family_tasks",), any_integration_groups=(("calendar", "email"),)),
    AutomationDefinition("daily_inbox_offers", "Daily inbox offers and coupon checks", "Money & shopping", "Find inbox offers, estimate savings and surface expiry dates and catches.", "daily", required_integrations=("email",)),
)


AUTOMATION_CATALOG: Tuple[AutomationDefinition, ...] = _AUTOMATIONS
AUTOMATION_BY_ID: Mapping[str, AutomationDefinition] = {item.id: item for item in _AUTOMATIONS}


def get_automation(automation_id: str) -> Optional[AutomationDefinition]:
    """Return a catalog definition by its stable identifier."""
    return AUTOMATION_BY_ID.get(str(automation_id or "").strip())


def list_automations(category: Optional[str] = None) -> Tuple[AutomationDefinition, ...]:
    """Return immutable catalog entries, optionally filtered by category."""
    if category is None:
        return AUTOMATION_CATALOG
    wanted = str(category).strip().casefold()
    return tuple(item for item in AUTOMATION_CATALOG if item.category.casefold() == wanted)


def _profile_value(profile: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = profile
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def automation_readiness(
    automation_id: str,
    *,
    profile: Mapping[str, Any],
    connected_integrations: Iterable[str] = (),
    registered_handlers: Iterable[str] = (),
    master_enabled: bool = False,
) -> Dict[str, Any]:
    """Evaluate whether an automation can be enabled; never assume a connector.

    A workflow is ready only when the global preference is enabled, its handler
    is registered, required profile fields are populated, and all required /
    at-least-one connector groups are satisfied.
    """
    definition = get_automation(automation_id)
    if definition is None:
        raise KeyError(f"Unknown automation: {automation_id}")

    connected = {str(item).strip().casefold() for item in connected_integrations}
    handlers = {str(item).strip() for item in registered_handlers}
    missing_profile = [
        path for path in definition.required_profile_fields
        if (value := _profile_value(profile, path)) is None
        or value == ""
        or value == []
    ]
    missing_integrations = [
        item for item in definition.required_integrations
        if item.casefold() not in connected
    ]
    missing_any_groups = [
        group for group in definition.any_integration_groups
        if not any(item.casefold() in connected for item in group)
    ]
    missing = []
    if not master_enabled:
        missing.append("personal automations are globally disabled")
    if definition.id not in handlers:
        missing.append("automation handler is not registered")
    if missing_profile:
        missing.append("profile: " + ", ".join(missing_profile))
    if missing_integrations:
        missing.append("integrations: " + ", ".join(missing_integrations))
    for group in missing_any_groups:
        missing.append("one of integrations: " + ", ".join(group))
    return {
        "automation_id": definition.id,
        "ready": not missing,
        "can_enable": not missing,
        "blockers": missing,
        "missing_profile_fields": missing_profile,
        "missing_integrations": missing_integrations,
        "missing_integration_groups": [list(group) for group in missing_any_groups],
    }
