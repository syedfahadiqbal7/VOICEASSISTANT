# ui/policy_viewer.py
"""
Phase 3 — Policy editor (role, deny tools, write roots, trusted opens).
"""

from __future__ import annotations

import customtkinter as ctk
from typing import Callable, Optional, List

_open_viewer = None


class PolicyViewer(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        policy,
        on_saved: Optional[Callable[[], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
    ):
        global _open_viewer
        if _open_viewer is not None:
            try:
                if _open_viewer.winfo_exists():
                    _open_viewer.lift()
                    _open_viewer.focus_force()
                    _open_viewer.reload()
                    self.withdraw()
                    self.after(10, self.destroy)
                    return
            except Exception:
                _open_viewer = None

        super().__init__(parent)
        _open_viewer = self
        self.title("Policy — role & allow/deny")
        self.geometry("560x620")
        self.policy = policy
        self.on_saved = on_saved
        self.on_transcript = on_transcript

        self.transient(parent)
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.lift()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        ctk.CTkLabel(
            self,
            text="Changes save to history/agent_policy.json and apply immediately.",
            text_color="gray",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=(12, 6))

        body = ctk.CTkScrollableFrame(self)
        body.pack(fill="both", expand=True, padx=16, pady=4)

        # Role
        ctk.CTkLabel(body, text="Role", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", pady=(8, 2)
        )
        self.role_var = ctk.StringVar(value="operator")
        self.role_menu = ctk.CTkOptionMenu(
            body,
            variable=self.role_var,
            values=["admin", "operator", "read_only"],
            width=220,
        )
        self.role_menu.pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(
            body,
            text="read_only blocks writes / PowerShell / installs",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

        # Allowed write roots
        ctk.CTkLabel(
            body, text="Allowed write roots (comma-separated)", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", pady=(14, 2))
        self.roots_entry = ctk.CTkEntry(body, width=480)
        self.roots_entry.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text="Example: Desktop, Documents, Downloads — empty = no path restriction",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

        # Denied tools
        ctk.CTkLabel(
            body, text="Denied tools (comma-separated tool names)", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", pady=(14, 2))
        self.denied_entry = ctk.CTkEntry(body, width=480)
        self.denied_entry.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text="Example: open_app, execute_powershell_admin",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

        # Deny extensions
        ctk.CTkLabel(
            body, text="Denied write extensions", font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", pady=(14, 2))
        self.ext_entry = ctk.CTkEntry(body, width=480)
        self.ext_entry.pack(anchor="w", pady=(0, 4))

        # Trusted scheduled opens
        ctk.CTkLabel(
            body,
            text="Trusted scheduled opens (auto-approve)",
            font=ctk.CTkFont(weight="bold"),
        ).pack(anchor="w", pady=(14, 2))
        self.trusted_box = ctk.CTkTextbox(body, height=100, width=480)
        self.trusted_box.pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            body,
            text="One name per line (notepad, calc.exe, …). File writes never auto-approve.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

        # Buttons
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(
            row, text="Save", width=100, fg_color="#8E44AD", hover_color="#6C3483", command=self._save
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Reload", width=90, command=self.reload).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row, text="Reset defaults", width=120, fg_color="#7F8C8D", command=self._reset_defaults
        ).pack(side="left")

        self.status = ctk.CTkLabel(self, text="", text_color="#1ABC9C")
        self.status.pack(anchor="w", padx=16, pady=(0, 10))

        self.reload()

    def _on_close(self):
        global _open_viewer
        _open_viewer = None
        self.destroy()

    def _split_csv(self, text: str) -> List[str]:
        parts = []
        for p in (text or "").replace("\n", ",").split(","):
            p = p.strip()
            if p:
                parts.append(p)
        return parts

    def reload(self):
        try:
            self.policy.load()
            d = self.policy.data
            self.role_var.set(str(d.get("role") or "operator"))
            roots = d.get("allowed_write_roots") or []
            self.roots_entry.delete(0, "end")
            self.roots_entry.insert(0, ", ".join(str(x) for x in roots))
            denied = d.get("denied_tools") or []
            self.denied_entry.delete(0, "end")
            self.denied_entry.insert(0, ", ".join(str(x) for x in denied))
            exts = d.get("deny_write_extensions") or []
            self.ext_entry.delete(0, "end")
            self.ext_entry.insert(0, ", ".join(str(x) for x in exts))
            trusted = d.get("auto_approve_scheduled_opens") or []
            self.trusted_box.delete("1.0", "end")
            self.trusted_box.insert("1.0", "\n".join(str(x) for x in trusted))
            self.status.configure(text="Loaded.", text_color="#1ABC9C")
        except Exception as e:
            self.status.configure(text=f"Load error: {e}", text_color="#E74C3C")

    def _save(self):
        try:
            self.policy.data["role"] = self.role_var.get()
            self.policy.data["allowed_write_roots"] = self._split_csv(self.roots_entry.get())
            self.policy.data["denied_tools"] = self._split_csv(self.denied_entry.get())
            exts = self._split_csv(self.ext_entry.get())
            # ensure leading dot
            self.policy.data["deny_write_extensions"] = [
                e if e.startswith(".") else f".{e}" for e in exts
            ]
            trusted_raw = self.trusted_box.get("1.0", "end").strip()
            trusted = []
            for line in trusted_raw.splitlines():
                line = line.strip()
                if line:
                    trusted.append(line)
            self.policy.data["auto_approve_scheduled_opens"] = trusted
            self.policy.save()
            self.status.configure(text="Saved — active now.", text_color="#27AE60")
            if self.on_transcript:
                self.on_transcript(
                    f"[Policy] Saved role={self.policy.role} "
                    f"roots={self.policy.data.get('allowed_write_roots')} "
                    f"denied={self.policy.data.get('denied_tools')}"
                )
            if self.on_saved:
                self.on_saved()
        except Exception as e:
            self.status.configure(text=f"Save error: {e}", text_color="#E74C3C")

    def _reset_defaults(self):
        try:
            from agent.policy import DEFAULT_POLICY
            import json

            self.policy.data = json.loads(json.dumps(DEFAULT_POLICY))
            self.policy.save()
            self.reload()
            self.status.configure(text="Reset to defaults and saved.", text_color="#F39C12")
            if self.on_transcript:
                self.on_transcript("[Policy] Reset to defaults.")
        except Exception as e:
            self.status.configure(text=f"Reset error: {e}", text_color="#E74C3C")


def open_policy_viewer(parent, policy, on_saved=None, on_transcript=None):
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
    return PolicyViewer(parent, policy, on_saved=on_saved, on_transcript=on_transcript)
