"""Read-only desktop catalog for personal automations and setup requirements."""
from __future__ import annotations

import customtkinter as ctk

from agent.automation_catalog import AUTOMATION_CATALOG


class AutomationCatalogViewer(ctk.CTkToplevel):
    def __init__(self, parent, profile_store):
        super().__init__(parent)
        self.title("Personal Automation Catalog")
        self.geometry("900x760")
        self.minsize(760, 560)
        self.profile_store = profile_store

        profile = profile_store.get_profile()
        global_enabled = bool(profile.get("settings", {}).get("personal_automations_enabled", False))

        ctk.CTkLabel(
            self,
            text="Personal Automation Catalog",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 4))

        global_text = (
            "Global preference: ON" if global_enabled
            else "Global preference: OFF (change this in Profile settings)"
        )
        ctk.CTkLabel(
            self,
            text=global_text,
            text_color=("#1E8449", "#58D68D") if global_enabled else ("#9A6700", "#F5C16C"),
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(0, 8))

        ctk.CTkLabel(
            self,
            text=(
                "This is a setup catalog, not an active automation dashboard. All workflows remain disabled "
                "until their real handler, required profile data and provider integrations are available. "
                "No email, calendar or other account is connected by this screen."
            ),
            justify="left",
            wraplength=850,
            text_color=("gray35", "gray70"),
        ).pack(anchor="w", padx=18, pady=(0, 12))

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
                card = ctk.CTkFrame(body, corner_radius=8)
                card.pack(fill="x", padx=6, pady=4)
                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=10, pady=(8, 2))
                ctk.CTkLabel(
                    top,
                    text=item.title,
                    font=ctk.CTkFont(size=13, weight="bold"),
                ).pack(side="left", anchor="w")
                ctk.CTkLabel(
                    top,
                    text="NOT ACTIVE",
                    text_color=("gray40", "gray65"),
                    font=ctk.CTkFont(size=10, weight="bold"),
                ).pack(side="right", anchor="e")
                ctk.CTkLabel(
                    card,
                    text=f"{item.description}\nCadence: {self._cadence(item)}",
                    justify="left",
                    wraplength=820,
                    text_color=("gray25", "gray75"),
                ).pack(anchor="w", padx=10, pady=(0, 8))

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


def open_automation_catalog_viewer(parent, profile_store):
    return AutomationCatalogViewer(parent, profile_store)
