"""Desktop editor for the encrypted local user profile."""
from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox
from typing import Callable, Optional


class ProfileViewer(ctk.CTkToplevel):
    def __init__(self, parent, profile_store, on_saved: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.title("Personal Profile & Preferences")
        self.geometry("900x820")
        self.minsize(760, 620)
        self.profile_store = profile_store
        self.on_saved = on_saved
        self.fields = {}

        ctk.CTkLabel(
            self,
            text="Personal Profile & Preferences",
            font=ctk.CTkFont(size=19, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            self,
            text=(
                "Saved locally using encryption. Assistant context is opt-in. "
                "Birthdays store month/day only—never a birth year. Do not enter passwords, API keys or payment data."
            ),
            justify="left",
            wraplength=840,
            text_color=("gray35", "gray70"),
        ).pack(anchor="w", padx=18, pady=(0, 10))

        self.body = ctk.CTkScrollableFrame(self, corner_radius=8)
        self.body.pack(fill="both", expand=True, padx=14, pady=6)

        self._build_settings()
        self._build_list_fields()
        self._build_structured_fields()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(6, 14))
        self.status = ctk.CTkLabel(footer, text="", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(footer, text="Save preferences", width=150, command=self._save).pack(side="right")

        self._load()

    def _build_settings(self):
        frame = ctk.CTkFrame(self.body)
        frame.pack(fill="x", padx=4, pady=5)
        ctk.CTkLabel(frame, text="Privacy and automation settings", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )
        self.context_var = ctk.BooleanVar(value=False)
        self.automation_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            frame,
            text="Allow selected profile preferences in the assistant's conversation context",
            variable=self.context_var,
        ).pack(anchor="w", padx=10, pady=3)
        ctk.CTkCheckBox(
            frame,
            text="Save preferences for upcoming personal automations (does not connect accounts or start schedules)",
            variable=self.automation_var,
        ).pack(anchor="w", padx=10, pady=3)
        timezone_row = ctk.CTkFrame(frame, fg_color="transparent")
        timezone_row.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(timezone_row, text="IANA timezone (optional)", width=180, anchor="w").pack(side="left")
        self.timezone_entry = ctk.CTkEntry(timezone_row, placeholder_text="e.g. Asia/Dubai", width=220)
        self.timezone_entry.pack(side="left")

    def _add_list_field(self, label: str, key: str, values=None, hint: str = ""):
        frame = ctk.CTkFrame(self.body)
        frame.pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(7, 1)
        )
        if hint:
            ctk.CTkLabel(frame, text=hint, text_color=("gray45", "gray65")).pack(
                anchor="w", padx=10, pady=(0, 2)
            )
        widget = ctk.CTkTextbox(frame, height=52)
        widget.pack(fill="x", padx=8, pady=(0, 8))
        if values:
            widget.insert("1.0", "\n".join(values))
        self.fields[key] = widget

    def _build_list_fields(self):
        self._add_list_field("Interests", "interests")
        self._add_list_field("Target job roles", "target_roles")
        self._add_list_field("Preferred job locations", "preferred_locations")
        self._add_list_field("Industries", "industries")
        self._add_list_field("Book genres", "book_genres")
        self._add_list_field("Movie genres", "movie_genres")
        self._add_list_field("TV / show genres", "tv_genres")
        self._add_list_field("News topics", "news_topics")
        self._add_list_field("Research topics", "research_topics")
        self._add_list_field("Cuisine preferences", "cuisines")
        self._add_list_field("Meal preferences", "meal_preferences")
        self._add_list_field("Workout preferences", "workout_preferences")
        self._add_list_field("Wardrobe items", "wardrobe_items")
        self._add_list_field("Favorite stores", "favorite_stores")

    def _build_structured_fields(self):
        frame = ctk.CTkFrame(self.body)
        frame.pack(fill="x", padx=4, pady=5)
        ctk.CTkLabel(frame, text="Career and language", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(row, text="Remote preference", width=150, anchor="w").pack(side="left")
        self.remote_var = ctk.StringVar(value="any")
        ctk.CTkOptionMenu(row, variable=self.remote_var, values=["any", "remote_only", "hybrid", "on_site"]).pack(side="left")
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(row2, text="Target language", width=150, anchor="w").pack(side="left")
        self.language_entry = ctk.CTkEntry(row2, width=180, placeholder_text="e.g. German")
        self.language_entry.pack(side="left", padx=(0, 14))
        ctk.CTkLabel(row2, text="Level", width=50, anchor="w").pack(side="left")
        self.level_var = ctk.StringVar(value="beginner")
        ctk.CTkOptionMenu(
            row2, variable=self.level_var,
            values=["beginner", "elementary", "intermediate", "upper_intermediate", "advanced", "fluent"],
            width=170,
        ).pack(side="left")
        row3 = ctk.CTkFrame(frame, fg_color="transparent")
        row3.pack(fill="x", padx=8, pady=(4, 8))
        ctk.CTkLabel(row3, text="Daily lesson minutes", width=150, anchor="w").pack(side="left")
        self.minutes_var = ctk.StringVar(value="10")
        ctk.CTkOptionMenu(row3, variable=self.minutes_var, values=[str(i) for i in range(1, 11)], width=80).pack(side="left")

        self._add_list_field(
            "Birthdays (one per line: Label | MM-DD | reminder days)",
            "birthdays_text",
            hint="Example: Family member | 04-12 | 30,7,1. Only month/day is stored; no birth year.",
        )
        self._add_list_field(
            "Shopping watchlist (one per line: Name | Category | target price | currency)",
            "watchlist_text",
            hint="Example: Noise cancelling headphones | Electronics | 250 | AED. Leave target price blank if not needed.",
        )

    @staticmethod
    def _read_lines(widget):
        return [line.strip() for line in widget.get("1.0", "end").splitlines() if line.strip()]

    def _load(self):
        try:
            profile = self.profile_store.get_profile()
        except Exception as error:
            self.status.configure(text=f"Profile unavailable: {type(error).__name__}", text_color="#E74C3C")
            return
        settings = profile["settings"]
        self.context_var.set(settings["assistant_context_enabled"])
        self.automation_var.set(settings["personal_automations_enabled"])
        self.timezone_entry.insert(0, settings["timezone"])
        career = profile["career"]
        self.remote_var.set(career["remote_preference"])
        self.language_entry.insert(0, profile["language"]["target_language"])
        self.level_var.set(profile["language"]["level"])
        self.minutes_var.set(str(profile["language"]["daily_minutes"]))

        sections = {
            "interests": profile["interests"],
            "target_roles": career["target_roles"],
            "preferred_locations": career["preferred_locations"],
            "industries": career["industries"],
            "book_genres": profile["content"]["book_genres"],
            "movie_genres": profile["content"]["movie_genres"],
            "tv_genres": profile["content"]["tv_genres"],
            "news_topics": profile["content"]["news_topics"],
            "research_topics": profile["content"]["research_topics"],
            "cuisines": profile["lifestyle"]["cuisines"],
            "meal_preferences": profile["lifestyle"]["meal_preferences"],
            "workout_preferences": profile["lifestyle"]["workout_preferences"],
            "wardrobe_items": profile["lifestyle"]["wardrobe_items"],
            "favorite_stores": profile["shopping"]["favorite_stores"],
        }
        for key, values in sections.items():
            self.fields[key].insert("1.0", "\n".join(values))
        birthdays = [
            f"{item['label']} | {item['month_day']} | {','.join(str(day) for day in item['reminder_days'])}"
            for item in profile["birthdays"]
        ]
        watchlist = [
            f"{item['name']} | {item['category']} | {item['target_price'] if item['target_price'] is not None else ''} | {item['currency']}"
            for item in profile["shopping"]["watchlist"]
        ]
        self.fields["birthdays_text"].insert("1.0", "\n".join(birthdays))
        self.fields["watchlist_text"].insert("1.0", "\n".join(watchlist))

    def _parse_birthdays(self):
        items = []
        for line in self._read_lines(self.fields["birthdays_text"]):
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 3:
                raise ValueError("Each birthday line must be: Label | MM-DD | 30,7,1")
            try:
                days = [int(part.strip()) for part in parts[2].split(",") if part.strip()]
            except ValueError as error:
                raise ValueError("Birthday reminder days must be numbers") from error
            items.append({"label": parts[0], "month_day": parts[1], "reminder_days": days})
        return items

    def _parse_watchlist(self):
        items = []
        for line in self._read_lines(self.fields["watchlist_text"]):
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 4:
                raise ValueError("Each watchlist line must be: Name | Category | target price | currency")
            price = None
            if parts[2]:
                try:
                    price = float(parts[2])
                except ValueError as error:
                    raise ValueError("Watchlist target price must be numeric") from error
            items.append({
                "name": parts[0],
                "category": parts[1],
                "target_price": price,
                "currency": parts[3] or "USD",
            })
        return items

    def _save(self):
        try:
            updates = {
                "settings": {
                    "assistant_context_enabled": bool(self.context_var.get()),
                    "personal_automations_enabled": bool(self.automation_var.get()),
                    "timezone": self.timezone_entry.get().strip(),
                },
                "interests": self._read_lines(self.fields["interests"]),
                "career": {
                    "target_roles": self._read_lines(self.fields["target_roles"]),
                    "preferred_locations": self._read_lines(self.fields["preferred_locations"]),
                    "industries": self._read_lines(self.fields["industries"]),
                    "remote_preference": self.remote_var.get(),
                },
                "content": {
                    key: self._read_lines(self.fields[key])
                    for key in ("book_genres", "movie_genres", "tv_genres", "news_topics", "research_topics")
                },
                "language": {
                    "target_language": self.language_entry.get().strip(),
                    "level": self.level_var.get(),
                    "daily_minutes": int(self.minutes_var.get()),
                },
                "lifestyle": {
                    key: self._read_lines(self.fields[key])
                    for key in ("cuisines", "meal_preferences", "workout_preferences", "wardrobe_items")
                },
                "shopping": {
                    "favorite_stores": self._read_lines(self.fields["favorite_stores"]),
                    "watchlist": self._parse_watchlist(),
                },
                "birthdays": self._parse_birthdays(),
            }
            self.profile_store.update_profile(updates)
            if self.on_saved:
                self.on_saved()
            self.status.configure(text="Profile saved securely.", text_color="#27AE60")
        except Exception as error:
            self.status.configure(text=f"Could not save profile: {error}", text_color="#E74C3C")
            messagebox.showerror("Profile validation", str(error), parent=self)


def open_profile_viewer(parent, profile_store, on_saved=None):
    return ProfileViewer(parent, profile_store, on_saved=on_saved)
