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

        self.logs = list(logs or [])
        self.page_size = 100
        self.page = 0
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

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=15, pady=(0, 12), sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        self.previous_btn = ctk.CTkButton(
            footer, text="Previous", width=90, command=lambda: self._change_page(-1)
        )
        self.previous_btn.grid(row=0, column=0, sticky="w")
        self.page_label = ctk.CTkLabel(footer, text="")
        self.page_label.grid(row=0, column=1, padx=8)
        self.next_btn = ctk.CTkButton(
            footer, text="Next", width=90, command=lambda: self._change_page(1)
        )
        self.next_btn.grid(row=0, column=2, sticky="e")

    def _populate(self):
        for child in self.log_frame.winfo_children():
            child.destroy()

        if not self.logs:
            ctk.CTkLabel(
                self.log_frame,
                text="No audit entries yet.",
                text_color="gray",
            ).grid(row=0, column=0, pady=20)
            self.page_label.configure(text="No entries")
            self.previous_btn.configure(state="disabled")
            self.next_btn.configure(state="disabled")
            return

        # Newest first, with bounded widget creation per page.
        ordered = list(reversed(self.logs))
        total_pages = max(1, (len(ordered) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, total_pages - 1))
        start = self.page * self.page_size
        visible = ordered[start:start + self.page_size]
        for idx, entry in enumerate(visible):
            self._create_entry(idx, entry)

        self.page_label.configure(
            text=f"Page {self.page + 1} of {total_pages}  •  Showing {start + 1}-{start + len(visible)} of {len(ordered)}"
        )
        self.previous_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page + 1 < total_pages else "disabled")

    def _change_page(self, delta: int):
        self.page += delta
        self._populate()

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