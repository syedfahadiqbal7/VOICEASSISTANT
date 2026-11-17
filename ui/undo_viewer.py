# ui/undo_viewer.py
"""
Phase 2 — Undo history panel (last file writes, LIFO).
"""

from __future__ import annotations

import customtkinter as ctk
from typing import Callable, Optional

_open_viewer = None


class UndoViewer(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        undo_manager,
        on_undo: Optional[Callable[[Callable[[], None]], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
    ):
        global _open_viewer
        super().__init__(parent)
        _open_viewer = self
        self.title("Undo History — File changes")
        self.geometry("640x480")
        self.undo = undo_manager
        self.on_undo = on_undo
        self.on_transcript = on_transcript

        self.transient(parent)
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.lift()
        self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        ctk.CTkLabel(
            self,
            text="Recent file writes (newest first). Undo always applies to the latest entry.",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        ).pack(anchor="w", padx=16, pady=(14, 4))

        self.list_frame = ctk.CTkScrollableFrame(self, height=340)
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=8)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 14))

        ctk.CTkButton(
            btn_row,
            text="Undo latest",
            width=120,
            fg_color="#27AE60",
            hover_color="#219653",
            command=self._undo_latest,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(btn_row, text="Refresh", width=100, command=self.reload).pack(
            side="left"
        )

        ctk.CTkLabel(
            btn_row,
            text="Only the top (newest) change can be undone next.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=10)

        self.reload()

    def _on_close(self):
        global _open_viewer
        _open_viewer = None
        self.destroy()

    def reload(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        try:
            entries = self.undo.list_stack()
        except Exception as e:
            ctk.CTkLabel(self.list_frame, text=f"Error: {e}").pack(anchor="w")
            return

        if not entries:
            ctk.CTkLabel(
                self.list_frame,
                text="No undo entries yet. Create/overwrite a file to populate history.",
                text_color="gray",
            ).pack(anchor="w", padx=8, pady=12)
            return

        for i, entry in enumerate(entries):
            row = ctk.CTkFrame(
                self.list_frame, fg_color=("gray92", "gray20"), corner_radius=8
            )
            row.pack(fill="x", padx=4, pady=4)

            path = entry.get("filepath") or "?"
            preview = (entry.get("content_preview") or "").replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:80] + "…"
            ts = entry.get("timestamp") or ""
            if len(ts) > 19:
                ts = ts[:19]
            kind = "NEW" if str(entry.get("backup_path") or "").endswith(".new") else "EDIT"

            text = f"[{kind}] {path}\n{ts}  ·  {preview or '(no preview)'}"
            ctk.CTkLabel(row, text=text, justify="left", anchor="w").pack(
                side="left", fill="x", expand=True, padx=10, pady=8
            )

            if i == 0:
                ctk.CTkButton(
                    row,
                    text="Undo",
                    width=80,
                    fg_color="#27AE60",
                    hover_color="#219653",
                    command=self._undo_latest,
                ).pack(side="right", padx=10, pady=8)
            else:
                ctk.CTkLabel(
                    row, text="(later)", text_color="gray", font=ctk.CTkFont(size=11)
                ).pack(side="right", padx=10, pady=8)

    def _undo_latest(self):
        try:
            if self.on_undo:
                # The application callback may run undo asynchronously. Refresh only
                # after it reports completion so the list cannot show stale entries.
                self.on_undo(self.reload)
                return
            result = self.undo.undo_last()
            msg = result.get("message") or "Undo done."
            if self.on_transcript:
                self.on_transcript(f"[Undo] {msg}")
        except Exception as e:
            if self.on_transcript:
                self.on_transcript(f"[Undo] Error: {e}")
        self.after(150, self.reload)


def open_undo_viewer(parent, undo_manager, on_undo=None, on_transcript=None):
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
    return UndoViewer(
        parent,
        undo_manager,
        on_undo=on_undo,
        on_transcript=on_transcript,
    )
