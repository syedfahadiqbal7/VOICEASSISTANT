# ui/scheduler_viewer.py
"""
Phase 2 — Production-grade Scheduled Jobs UI
- Easy form for lay users (no need to memorize prompt syntax)
- Job list with cancel/refresh
- Singleton window, stays visible above main app
"""

from __future__ import annotations

import customtkinter as ctk

from agent.scheduler_ui_state import job_display_state
from datetime import datetime, timedelta
from typing import Callable, Optional

_open_viewer = None

COMMON_APPS = [
    "notepad",
    "calculator",
    "chrome",
    "edge",
    "explorer",
    "cmd",
    "powershell",
]


class SchedulerViewer(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        scheduler,
        on_fill_message: Optional[Callable[[str], None]] = None,
        on_schedule_direct: Optional[Callable[[str], None]] = None,
    ):
        global _open_viewer
        super().__init__(parent)
        _open_viewer = self
        self.title("Scheduler — Schedule a Task")
        self.geometry("700x620")
        self.scheduler = scheduler
        self.on_fill_message = on_fill_message
        self.on_schedule_direct = on_schedule_direct

        self.transient(parent)
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.lift()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_form()
        self._build_list()
        self.reload()

    def _on_close(self):
        global _open_viewer
        _open_viewer = None
        self.destroy()

    def _build_form(self):
        form = ctk.CTkFrame(self, corner_radius=10)
        form.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            form,
            text="Create a scheduled task",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 8))

        # What to do
        ctk.CTkLabel(form, text="What should happen?").grid(
            row=1, column=0, sticky="w", padx=12, pady=4
        )
        self.action_var = ctk.StringVar(value="Open an app")
        self.action_menu = ctk.CTkOptionMenu(
            form,
            variable=self.action_var,
            values=["Open an app", "Create a text file on Desktop", "Custom instruction"],
            command=self._on_action_change,
            width=280,
        )
        self.action_menu.grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=4)

        # App row
        self.app_label = ctk.CTkLabel(form, text="Application")
        self.app_label.grid(row=2, column=0, sticky="w", padx=12, pady=4)
        self.app_var = ctk.StringVar(value="notepad")
        self.app_menu = ctk.CTkOptionMenu(
            form, variable=self.app_var, values=COMMON_APPS, width=200
        )
        self.app_menu.grid(row=2, column=1, sticky="w", padx=8, pady=4)

        # File row
        self.file_label = ctk.CTkLabel(form, text="File name")
        self.file_entry = ctk.CTkEntry(form, width=180, placeholder_text="pulse.txt")
        self.content_label = ctk.CTkLabel(form, text="File content")
        self.content_entry = ctk.CTkEntry(form, width=220, placeholder_text="alive")

        # Custom row
        self.custom_label = ctk.CTkLabel(form, text="Instruction")
        self.custom_entry = ctk.CTkTextbox(form, height=60, width=400)

        # When
        ctk.CTkLabel(form, text="When?").grid(row=5, column=0, sticky="w", padx=12, pady=4)
        self.when_var = ctk.StringVar(value="Once, after a delay")
        self.when_menu = ctk.CTkOptionMenu(
            form,
            variable=self.when_var,
            values=["Once, after a delay", "Repeat every X minutes", "Daily", "Weekly", "Monthly"],
            width=280,
        )
        self.when_menu.grid(row=5, column=1, columnspan=2, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(form, text="Minutes").grid(row=6, column=0, sticky="w", padx=12, pady=4)
        self.minutes_entry = ctk.CTkEntry(form, width=80)
        self.minutes_entry.insert(0, "2")
        self.minutes_entry.grid(row=6, column=1, sticky="w", padx=8, pady=4)

        self.calendar_frame = ctk.CTkFrame(form, fg_color="transparent")
        self.calendar_time_entry = ctk.CTkEntry(
            self.calendar_frame, width=80, placeholder_text="09:00"
        )
        self.calendar_time_entry.insert(0, "09:00")
        self.calendar_timezone_entry = ctk.CTkEntry(
            self.calendar_frame, width=145, placeholder_text="Asia/Dubai"
        )
        self.calendar_timezone_entry.insert(0, "Asia/Dubai")
        self.calendar_weekdays_entry = ctk.CTkEntry(
            self.calendar_frame, width=120, placeholder_text="Mon,Wed,Fri"
        )
        self.calendar_weekdays_entry.insert(0, "Mon,Wed,Fri")
        self.calendar_monthday_entry = ctk.CTkEntry(
            self.calendar_frame, width=55, placeholder_text="1"
        )
        self.calendar_monthday_entry.insert(0, "1")
        for column, (label, widget) in enumerate((
            ("Local time", self.calendar_time_entry),
            ("IANA timezone", self.calendar_timezone_entry),
            ("Weekdays", self.calendar_weekdays_entry),
            ("Day of month", self.calendar_monthday_entry),
        )):
            ctk.CTkLabel(self.calendar_frame, text=label).grid(
                row=0, column=column, padx=3, pady=(2, 0)
            )
            widget.grid(row=1, column=column, padx=3, pady=(0, 3))

        # Preview
        ctk.CTkLabel(form, text="Preview command").grid(
            row=8, column=0, sticky="nw", padx=12, pady=8
        )
        self.preview = ctk.CTkLabel(
            form,
            text="",
            justify="left",
            anchor="w",
            text_color="#1ABC9C",
            font=ctk.CTkFont(size=12),
            wraplength=420,
        )
        self.preview.grid(row=8, column=1, columnspan=2, sticky="w", padx=8, pady=8)

        # Buttons
        btn_row = ctk.CTkFrame(form, fg_color="transparent")
        btn_row.grid(row=9, column=0, columnspan=3, sticky="ew", padx=12, pady=(4, 14))

        ctk.CTkButton(
            btn_row,
            text="1) Fill message box",
            width=150,
            fg_color="#2980B9",
            command=self._fill_only,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text="2) Schedule now",
            width=140,
            fg_color="#27AE60",
            hover_color="#219653",
            command=self._schedule_now,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="Update preview", width=120, command=self._update_preview
        ).pack(side="left")

        self._on_action_change(self.action_var.get())
        self._on_when_change(self.when_var.get())
        self._update_preview()

        # Live preview on change
        self.minutes_entry.bind("<KeyRelease>", lambda e: self._update_preview())
        for entry in (self.calendar_time_entry, self.calendar_timezone_entry, self.calendar_weekdays_entry, self.calendar_monthday_entry):
            entry.bind("<KeyRelease>", lambda e: self._update_preview())
        self.file_entry.bind("<KeyRelease>", lambda e: self._update_preview())
        self.content_entry.bind("<KeyRelease>", lambda e: self._update_preview())
        self.custom_entry.bind("<KeyRelease>", lambda e: self._update_preview())
        self.when_menu.configure(command=self._on_when_change)
        self.app_menu.configure(command=lambda _=None: self._update_preview())

    def _on_action_change(self, value: str):
        # Hide all optional
        for w in (
            self.app_label,
            self.app_menu,
            self.file_label,
            self.file_entry,
            self.content_label,
            self.content_entry,
            self.custom_label,
            self.custom_entry,
        ):
            w.grid_remove()

        if value == "Open an app":
            self.app_label.grid(row=2, column=0, sticky="w", padx=12, pady=4)
            self.app_menu.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        elif value == "Create a text file on Desktop":
            self.file_label.grid(row=2, column=0, sticky="w", padx=12, pady=4)
            self.file_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)
            self.content_label.grid(row=3, column=0, sticky="w", padx=12, pady=4)
            self.content_entry.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        else:
            self.custom_label.grid(row=2, column=0, sticky="nw", padx=12, pady=4)
            self.custom_entry.grid(row=2, column=1, columnspan=2, sticky="w", padx=8, pady=4)
        self._update_preview()

    def _on_when_change(self, value: str):
        if value in {"Daily", "Weekly", "Monthly"}:
            self.calendar_frame.grid(row=7, column=0, columnspan=3, sticky="w", padx=8, pady=3)
            self.minutes_entry.configure(state="disabled")
        else:
            self.calendar_frame.grid_remove()
            self.minutes_entry.configure(state="normal")
        self._update_preview()

    def _build_action_body(self) -> str:
        action = self.action_var.get()
        if action == "Open an app":
            app = self.app_var.get().strip() or "notepad"
            if app not in COMMON_APPS:
                raise ValueError("Choose an application from the approved list.")
            body = f"Open {app}"
        elif action == "Create a text file on Desktop":
            name = (self.file_entry.get() or "note.txt").strip()
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("Enter a file name only; folder paths are not allowed.")
            if not name.lower().endswith(".txt"):
                name += ".txt"
            content = (self.content_entry.get() or "OK").strip()
            if len(content) > 5000:
                raise ValueError("File content is limited to 5000 characters.")
            body = f"Create a file named {name} on desktop with text {content}"
        else:
            body = self.custom_entry.get("1.0", "end").strip() or "Open notepad"
            if len(body) > 10000:
                raise ValueError("Custom instructions are limited to 10000 characters.")

        return body

    def _calendar_rule(self):
        from agent.calendar_recurrence import validate_calendar_recurrence

        frequency = self.when_var.get().lower()
        local_time = self.calendar_time_entry.get().strip()
        timezone_name = self.calendar_timezone_entry.get().strip()
        rule = {
            "type": "calendar",
            "frequency": frequency,
            "local_time": local_time,
            "timezone": timezone_name,
        }
        if frequency == "weekly":
            day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3,
                       "fri": 4, "sat": 5, "sun": 6}
            raw_days = [part.strip().lower()[:3]
                        for part in self.calendar_weekdays_entry.get().split(",")]
            if not raw_days or any(day not in day_map for day in raw_days):
                raise ValueError("Weekdays must be comma-separated names, e.g. Mon,Wed,Fri.")
            rule["weekdays"] = [day_map[day] for day in raw_days]
        elif frequency == "monthly":
            raw_day = self.calendar_monthday_entry.get().strip()
            if not raw_day.isdigit():
                raise ValueError("Day of month must be a number from 1 to 31.")
            rule["day_of_month"] = int(raw_day)
        return validate_calendar_recurrence(rule)

    def _build_prompt(self) -> str:
        when_var = getattr(self, "when_var", None)
        when = when_var.get() if when_var is not None else "Once, after a delay"
        body = SchedulerViewer._build_action_body(self)
        if when in {"Daily", "Weekly", "Monthly"}:
            rule = self._calendar_rule()
            return f"Calendar schedule: {rule['frequency']} at {rule['local_time']} ({rule['timezone']}): {body}"
        raw_minutes = self.minutes_entry.get().strip()
        if not raw_minutes.isdigit():
            raise ValueError("Minutes must be a whole number between 1 and 525600.")
        minutes = int(raw_minutes)
        if not 1 <= minutes <= 525600:
            raise ValueError("Minutes must be between 1 and 525600.")
        if when.startswith("Once"):
            return f"schedule in {minutes} minutes: {body}"
        return f"schedule every {minutes} minutes: {body}"

    def _update_preview(self, _=None):
        try:
            self.preview.configure(text=self._build_prompt(), text_color="#1ABC9C")
        except ValueError as error:
            self.preview.configure(text=f"Invalid schedule: {error}", text_color="#E74C3C")

    def _clear_form(self):
        """Reset form to defaults after successful Fill / Schedule now."""
        self.action_var.set("Open an app")
        self.app_var.set("notepad")
        self.when_var.set("Once, after a delay")
        self._on_when_change(self.when_var.get())
        self.minutes_entry.delete(0, "end")
        self.minutes_entry.insert(0, "2")
        self.file_entry.delete(0, "end")
        self.content_entry.delete(0, "end")
        try:
            self.custom_entry.delete("1.0", "end")
        except Exception:
            pass
        self._on_action_change("Open an app")
        self._update_preview()

    def _fill_only(self):
        """Only put the generated command into the main message box. Does not schedule."""
        try:
            prompt = self._build_prompt()
        except ValueError:
            self._update_preview()
            return
        if not self.on_fill_message:
            self.preview.configure(text="Message box is unavailable.", text_color="#E74C3C")
            return
        self.on_fill_message(prompt)
        self._clear_form()

    def _schedule_now(self):
        """Schedule immediately. Does NOT write anything into the message box."""
        try:
            prompt = self._build_prompt()
        except ValueError:
            self._update_preview()
            return
        if self.when_var.get() in {"Daily", "Weekly", "Monthly"}:
            try:
                body = self._build_action_body()
                recurrence = self._calendar_rule()
                self.scheduler.add_calendar(body, recurrence)
                self._clear_form()
                self.preview.configure(
                    text="Calendar schedule created successfully.", text_color="#27AE60"
                )
                self.after(200, self.reload)
            except Exception as error:
                self.preview.configure(
                    text=f"Could not create calendar schedule: {error}", text_color="#E74C3C"
                )
            return
        if not self.on_schedule_direct:
            self.preview.configure(text="Scheduler action is unavailable.", text_color="#E74C3C")
            return
        self.on_schedule_direct(prompt)
        self._clear_form()
        self.after(200, self.reload)

    def _build_list(self):
        ctk.CTkLabel(
            self,
            text="Your scheduled jobs",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(8, 4))

        self.list_frame = ctk.CTkScrollableFrame(self, height=200)
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=4)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(btn_row, text="Refresh list", width=110, command=self.reload).pack(
            side="left"
        )
        ctk.CTkLabel(
            btn_row,
            text="Jobs run only while the desktop app is open.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=10)

    def reload(self):
        if not hasattr(self, "list_frame"):
            return
        for w in self.list_frame.winfo_children():
            w.destroy()
        try:
            jobs = self.scheduler.list_jobs()
        except Exception as e:
            ctk.CTkLabel(self.list_frame, text=f"Error: {e}").pack(anchor="w")
            return
        if not jobs:
            ctk.CTkLabel(
                self.list_frame, text="No jobs yet. Use the form above.", text_color="gray"
            ).pack(anchor="w", padx=8, pady=8)
            return
        for job in jobs:
            row = ctk.CTkFrame(self.list_frame, fg_color=("gray92", "gray20"), corner_radius=8)
            row.pack(fill="x", padx=4, pady=4)
            status, diagnostics, can_cancel = job_display_state(job)
            timing = ""
            if status == "Pending":
                timing = f"next: {job.get('run_at', '')}"
            elif status == "Running":
                timing = f"started: {job.get('started_at', 'in progress')}"
            elif status in {"Completed", "Failed"}:
                timing = f"last run: {job.get('last_run') or 'not recorded'}"
            repeat = job.get("repeat")
            if isinstance(repeat, dict) and repeat.get("type") == "calendar":
                cadence = (
                    f"{repeat.get('frequency', 'calendar')} at "
                    f"{repeat.get('local_time', 'unspecified')} "
                    f"({repeat.get('timezone', 'unspecified timezone')})"
                )
                if repeat.get("frequency") == "weekly":
                    day_names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
                    days = ", ".join(
                        day_names[day] for day in repeat.get("weekdays", [])
                        if isinstance(day, int) and 0 <= day < 7
                    )
                    cadence += f" · {days}"
                elif repeat.get("frequency") == "monthly":
                    cadence += f" · day {repeat.get('day_of_month', '?')}"
                timing = " · ".join(part for part in (timing, cadence) if part)
            elif isinstance(repeat, dict) and repeat.get("every_minutes") is not None:
                timing = " · ".join(
                    part for part in (timing, f"every {repeat['every_minutes']} minutes") if part
                )
            details = [part for part in (timing, diagnostics) if part]
            text = (
                f"[{job.get('id')}] {status}"
                + (f"  |  {' | '.join(details)}" if details else "")
                + f"\n{job.get('label') or job.get('prompt', '')}"
            )
            ctk.CTkLabel(row, text=text, justify="left", anchor="w").pack(
                side="left", fill="x", expand=True, padx=10, pady=8
            )
            if can_cancel:
                jid = job.get("id")
                ctk.CTkButton(
                    row,
                    text="Cancel",
                    width=80,
                    fg_color="#C0392B",
                    hover_color="#922B21",
                    command=lambda i=jid: self._cancel(i),
                ).pack(side="right", padx=10, pady=8)

    def _cancel(self, job_id: str):
        try:
            cancelled = self.scheduler.cancel(job_id)
            if cancelled is False:
                self.preview.configure(text=f"Could not cancel scheduled job {job_id}.", text_color="#E74C3C")
            else:
                self.preview.configure(text=f"Scheduled job {job_id} cancelled.", text_color="#27AE60")
        except Exception as error:
            self.preview.configure(text=f"Cancel failed: {error}", text_color="#E74C3C")
        self.reload()


def open_scheduler_viewer(parent, scheduler, on_fill_message=None, on_schedule_direct=None):
    global _open_viewer
    if _open_viewer is not None:
        try:
            if _open_viewer.winfo_exists():
                _open_viewer.lift()
                _open_viewer.focus_force()
                _open_viewer.reload()
                return _open_viewer
        except Exception:
            _open_viewer = None
    return SchedulerViewer(
        parent,
        scheduler,
        on_fill_message=on_fill_message,
        on_schedule_direct=on_schedule_direct,
    )
