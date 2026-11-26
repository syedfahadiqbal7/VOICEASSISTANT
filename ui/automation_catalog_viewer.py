"""Personal automation catalog with honest readiness and activation status."""
from __future__ import annotations

import customtkinter as ctk

from agent.automation_catalog import AUTOMATION_CATALOG, automation_readiness


class AutomationCatalogViewer(ctk.CTkToplevel):
    def __init__(self, parent, profile_store, scheduler=None, on_birthday_toggle=None):
        super().__init__(parent)
        self.title("Personal Automation Catalog")
        self.geometry("920x780")
        self.minsize(760, 560)
        self.profile_store = profile_store
        self.scheduler = scheduler
        self.on_birthday_toggle = on_birthday_toggle

        self.profile = profile_store.get_profile()
        self.global_enabled = bool(self.profile.get("settings", {}).get("personal_automations_enabled", False))

        ctk.CTkLabel(
            self,
            text="Personal Automation Catalog",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 4))

        global_text = (
            "Global preference: ON" if self.global_enabled
            else "Global preference: OFF (change this in Profile settings)"
        )
        ctk.CTkLabel(
            self,
            text=global_text,
            text_color=("#1E8449", "#58D68D") if self.global_enabled else ("#9A6700", "#F5C16C"),
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(0, 8))

        ctk.CTkLabel(
            self,
            text=(
                "Only workflows with a real handler and all required profile/provider setup can be enabled. "
                "No email, calendar or other account is connected by this catalog. "
                "Local schedules run only while the desktop application is open."
            ),
            justify="left",
            wraplength=870,
            text_color=("gray35", "gray70"),
        ).pack(anchor="w", padx=18, pady=(0, 12))

        self.status = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=870)
        self.status.pack(fill="x", padx=18, pady=(0, 6))

        body = ctk.CTkScrollableFrame(self, label_text=f"{len(AUTOMATION_CATALOG)} available workflow definitions")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        categories = {}
        for item in AUTOMATION_CATALOG:
            categories.setdefault(item.category, []).append(item)

        for category in sorted(categories):
            ctk.CTkLabel(
                body,
                text=category,
                font=ctk.CTkFont(size=15, weight="bold"),
            ).pack(anchor="w", padx=8, pady=(12, 4))
            for item in categories[category]:
                self._render_item(body, item)

    def _render_item(self, parent, item):
        card = ctk.CTkFrame(parent, corner_radius=8)
        card.pack(fill="x", padx=6, pady=4)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))

        profile = self.profile_store.get_profile()
        settings = profile.get("settings", {})
        readiness = automation_readiness(
            item.id,
            profile=profile,
            connected_integrations=(),
            registered_handlers={"birthday_reminders"},
            master_enabled=bool(settings.get("personal_automations_enabled", False)),
        )
        enabled = bool(settings.get("birthday_reminders_enabled", False)) if item.id == "birthday_reminders" else False
        active_job = self._birthday_job()
        active = bool(active_job and active_job.get("enabled") and active_job.get("status") in {"pending", "running"})

        if item.id == "birthday_reminders":
            if active:
                status_text = "ACTIVE · LOCAL DESKTOP"
            elif enabled:
                status_text = "ENABLED · SCHEDULE NOT ACTIVE"
            elif readiness["can_enable"]:
                status_text = "READY TO ENABLE"
            else:
                status_text = "BLOCKED · SETUP REQUIRED"
        else:
            status_text = "NOT ACTIVE · IMPLEMENTATION PENDING"

        ctk.CTkLabel(
            top,
            text=item.title,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", anchor="w")
        ctk.CTkLabel(
            top,
            text=status_text,
            text_color=("#1E8449", "#58D68D") if active else ("#9A6700", "#F5C16C"),
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(side="right", anchor="e")

        detail = f"{item.description}\nCadence: {self._cadence(item)}\nSetup: {self._requirements(item)}"
        blockers = readiness["blockers"]
        if item.id == "birthday_reminders" and blockers and not enabled:
            detail += "\nCannot enable yet: " + "; ".join(blockers)
        elif item.id != "birthday_reminders":
            detail += "\nStatus: no executable workflow handler is registered yet."
            if blockers:
                detail += "\nRemaining setup: " + "; ".join(blockers[:3])
        ctk.CTkLabel(
            card,
            text=detail,
            justify="left",
            wraplength=850,
            text_color=("gray25", "gray75"),
        ).pack(anchor="w", padx=10, pady=(0, 8))

        if item.id == "birthday_reminders":
            button = ctk.CTkButton(
                card,
                text="Disable birthday reminders" if enabled else "Enable birthday reminders",
                width=190,
                state="normal" if enabled or readiness["can_enable"] else "disabled",
                command=lambda value=not enabled: self._toggle_birthday(value),
            )
            button.pack(anchor="e", padx=10, pady=(0, 8))

    def _birthday_job(self):
        if self.scheduler is None:
            return None
        return next(
            (job for job in self.scheduler.list_jobs() if job.get("prompt") == "__automation__:birthday_reminders"),
            None,
        )

    def _toggle_birthday(self, enabled: bool):
        if self.on_birthday_toggle is None:
            self.status.configure(text="Birthday reminder activation is unavailable.", text_color="#E74C3C")
            return
        try:
            result = self.on_birthday_toggle(enabled)
            if result.get("active"):
                self.status.configure(
                    text=f"Birthday reminders are scheduled for {self.profile_store.get_profile()['settings']['birthday_reminder_time']} local time. The desktop app must remain open.",
                    text_color="#27AE60",
                )
            else:
                self.status.configure(text=result.get("reason", "Birthday reminders are disabled."), text_color="#9A6700")
            self.destroy()
        except Exception as error:
            self.status.configure(text=f"Could not update birthday reminders: {error}", text_color="#E74C3C")

    @staticmethod
    def _requirements(item) -> str:
        requirements = []
        if item.required_profile_fields:
            requirements.append("Profile: " + ", ".join(item.required_profile_fields))
        if item.required_integrations:
            requirements.append("Integrations: " + ", ".join(item.required_integrations))
        for group in item.any_integration_groups:
            requirements.append("One of: " + " or ".join(group))
        return "; ".join(requirements) if requirements else "No additional profile or provider setup"

    @staticmethod
    def _cadence(item) -> str:
        if item.weekdays:
            names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            days = ", ".join(names[day] for day in item.weekdays)
            return f"Weekly ({days})"
        if item.cadence == "event" and item.days_before is not None:
            return f"Event-driven ({item.days_before} days before)"
        return {
            "daily": "Daily",
            "weekly": "Weekly",
            "monthly": "Monthly",
            "event": "Event-driven",
        }.get(item.cadence, item.cadence)


def open_automation_catalog_viewer(parent, profile_store, scheduler=None, on_birthday_toggle=None):
    return AutomationCatalogViewer(
        parent,
        profile_store,
        scheduler=scheduler,
        on_birthday_toggle=on_birthday_toggle,
    )
