# Personal Automation Catalog

This document describes the current declarative catalog in `agent/automation_catalog.py`.

## Execution and safety contract

- Catalog entries are definitions, not running automations.
- Every entry defaults to disabled.
- An automation can be enabled only when the global personal-automation setting is on, a real handler is registered, required profile values exist, and all required connectors are connected.
- Where a definition lists an alternative connector group, at least one member of that group must be connected.
- The catalog does not create OAuth grants, connect email/calendar accounts, create schedules, or deliver notifications.
- Scheduled jobs continue to run only while the desktop application is running.
- External side effects must use the scheduler's stable `execution_id` as an idempotency key at the integration boundary.
- No real account integration is represented as connected by default.

## Workflow definitions

| ID | Workflow | Cadence | Required setup |
|---|---|---|---|
| `industry_news_weekly` | Track industry news | Weekly | Industries, web research |
| `book_recommendations_monthly` | Recommend three books | Monthly | Book genres, web research |
| `daily_language_lesson` | Daily language lesson under 10 minutes | Daily | Target language and level |
| `saturday_home_task` | Saturday home task | Saturday | None |
| `monthly_budget_template` | Monthly blank budget template | Monthly | None; no bank access |
| `job_search_mon_thu` | Job matches | Monday and Thursday | Target roles, locations, job sources |
| `friday_movies_shows` | Three Friday movie/show picks | Friday | Movie/TV genres, web research |
| `weekly_store_coupons` | Weekly favorite-store coupons | Weekly | Favorite stores, web research |
| `daily_weather_outfit` | Weather-based outfit | Daily | Wardrobe, timezone, weather |
| `thursday_weekend_ideas` | Weekend ideas | Thursday | Local discovery |
| `daily_news_roundup` | Daily news roundup | Daily | News topics, news sources |
| `weekly_concerts` | New concerts | Weekly | Preferred locations, event sources |
| `weekly_dinners_grocery` | Five dinners and grocery list | Weekly | Cuisines and meal preferences |
| `saturday_article` | Saturday article | Saturday | Interests, web research |
| `topic_change_alerts` | Important topic-change alerts | Event-driven | News topics and news sources |
| `weekly_product_watchlist` | Product shortlist changes | Weekly | Product watchlist and price sources |
| `weekly_workout_plan` | Weekly workout plan | Weekly | Workout preferences |
| `friday_research_roundup` | Friday research roundup | Friday | Research topics and research sources |
| `daily_three_day_forecast` | Three-day forecast | Daily | Timezone and weather |
| `birthday_gift_planning` | Birthday gift planning | 30 days before birthday | Saved birthdays and web research |
| `daily_price_alerts` | Price alerts | Threshold/event-driven | Product watchlist and price sources |
| `birthday_reminders` | Birthday reminders | Per saved reminder day | Saved birthdays |
| `weekly_email_declutter` | Weekly email declutter | Weekly | Email connector; cleanup requires approval |
| `free_trial_expiry_reminders` | Free-trial expiry reminders | 2 days before expiry | Email connector |
| `weekday_work_email_briefing` | Urgent work email, replies owed and next steps | Weekdays | Email connector |
| `daily_company_news` | Company news | Daily | News sources and selected companies |
| `competitor_monitoring` | Competitor monitoring | Weekly | Web research and selected competitors |
| `weekly_application_updates` | Job application updates | Weekly | Job tracker |
| `weekly_deadlines_prep` | Deadlines and preparation | Weekly | Calendar or email connector |
| `daily_package_tracking` | Inbox delivery updates | Daily | Email and carrier tracking |
| `return_deadline_reminders` | Return deadline reminders | Daily | Email connector |
| `renewal_reminders` | Renewal reminders | Daily | Email connector |
| `sunday_family_tasks` | Deadlines, replies and family to-dos | Sunday | Family task source plus calendar or email |
| `daily_inbox_offers` | Inbox offers, savings and catches | Daily | Email connector |

Times are intentionally not hard-coded. Users choose local delivery times and timezone in the automation setup flow; the current catalog only captures cadence and weekday constraints.

## Connector identifiers

The readiness API currently recognizes these capability identifiers: `web_research`, `job_sources`, `news_sources`, `event_sources`, `local_discovery`, `price_sources`, `research_sources`, `weather`, `email`, `calendar`, `carrier_tracking`, `job_tracker`, and `family_tasks`.

These are capability names used for gating, not evidence of a working provider connection. Provider-specific OAuth, token storage, revocation and live API validation are separate implementation work.
