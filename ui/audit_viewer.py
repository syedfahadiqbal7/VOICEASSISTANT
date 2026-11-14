# ui/audit_viewer.py
import customtkinter as ctk
from typing import List, Dict
from datetime import datetime


class AuditLogViewer(ctk.CTkToplevel):
    def __init__(self, parent, logs: List[Dict]):
        super().__init__(parent)
        self.title("Audit Log")
        self.geometry("900x600")
        self.minsize(700, 450)
        self.transient(parent)
        self.grab_set()

        self.logs = logs
        self._build_ui()
        self._populate()

        # Center
        self.update_idletasks()
        x = parent.winfo_rootx() + 40
        y = parent.winfo_rooty() + 40
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="ew")

        ctk.CTkLabel(
            header,
            text="Security Audit Log",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            header,
            text="Close",
            width=90,
            command=self.destroy,
        ).pack(side="right")

        # Scrollable log area
        self.log_frame = ctk.CTkScrollableFrame(self)
        self.log_frame.grid(row=1, column=0, padx=15, pady=10, sticky="nsew")
        self.log_frame.grid_columnconfigure(0, weight=1)

    def _populate(self):
        if not self.logs:
            ctk.CTkLabel(
                self.log_frame,
                text="No audit entries yet.",
                text_color="gray",
            ).grid(row=0, column=0, pady=20)
            return

        # Show newest first
        for idx, entry in enumerate(reversed(self.logs)):
            self._create_entry(idx, entry)

    def _create_entry(self, idx: int, entry: Dict):
        frame = ctk.CTkFrame(self.log_frame, corner_radius=6)
        frame.grid(row=idx, column=0, sticky="ew", pady=4, padx=4)
        frame.grid_columnconfigure(1, weight=1)

        # Timestamp
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        ctk.CTkLabel(
            frame,
            text=ts,
            font=ctk.CTkFont(size=11),
            text_color="#95A5A6",
            width=140,
        ).grid(row=0, column=0, padx=8, pady=6, sticky="w")

        # Status badge
        approved = entry.get("approved", False)
        badge_text = "APPROVED" if approved else "DENIED"
        badge_color = "#27AE60" if approved else "#C0392B"

        ctk.CTkLabel(
            frame,
            text=badge_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=badge_color,
            width=80,
        ).grid(row=0, column=1, padx=5, sticky="w")

        # Action
        action = entry.get("action", "unknown")
        ctk.CTkLabel(
            frame,
            text=action,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=2, padx=8, sticky="w")

        # Details (truncated)
        details = entry.get("details", "")[:100].replace("\n", " ")
        ctk.CTkLabel(
            frame,
            text=details,
            font=ctk.CTkFont(size=11),
            text_color="#BDC3C7",
            anchor="w",
        ).grid(row=1, column=0, columnspan=3, padx=8, pady=(0, 6), sticky="ew")