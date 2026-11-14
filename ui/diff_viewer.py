# ui/diff_viewer.py
import customtkinter as ctk
from typing import Optional
import difflib
import os


class DiffViewer(ctk.CTkToplevel):
    """Modal window that shows a unified diff before writing a file."""

    def __init__(self, parent, filepath: str, new_content: str, on_confirm, on_cancel):
        super().__init__(parent)
        self.title("Code Diff Preview")
        self.geometry("900x650")
        self.minsize(700, 500)
        self.transient(parent)
        self.grab_set()

        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.filepath = filepath
        self.new_content = new_content

        self._build_ui()
        self._load_diff()

        # Center the window
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - 450
        y = parent.winfo_rooty() + 40
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="ew")

        ctk.CTkLabel(
            header,
            text="Review Changes Before Writing",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        self.path_label = ctk.CTkLabel(
            header,
            text=self.filepath,
            font=ctk.CTkFont(size=12),
            text_color="#3498DB",
        )
        self.path_label.pack(side="right")

        # Diff area
        self.diff_box = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="none",
        )
        self.diff_box.grid(row=1, column=0, padx=15, pady=10, sticky="nsew")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=15, pady=(5, 15), sticky="ew")

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            fg_color="#7F8C8D",
            hover_color="#636E72",
            width=120,
            command=self._cancel,
        ).pack(side="left")

        ctk.CTkButton(
            btn_frame,
            text="Approve & Write File",
            fg_color="#27AE60",
            hover_color="#219653",
            width=160,
            command=self._confirm,
        ).pack(side="right")

    def _load_diff(self):
        old_lines = []
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    old_lines = f.readlines()
            except Exception:
                old_lines = []

        new_lines = self.new_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{os.path.basename(self.filepath)}",
            tofile=f"b/{os.path.basename(self.filepath)}",
            lineterm="",
        )

        diff_text = "\n".join(diff)
        if not diff_text.strip():
            diff_text = "(No differences – file content is identical)"

        self.diff_box.insert("0.0", diff_text)
        self.diff_box.configure(state="disabled")

        # Simple color highlighting
        self._highlight_diff()

    def _highlight_diff(self):
        content = self.diff_box.get("0.0", "end")
        self.diff_box.configure(state="normal")

        for i, line in enumerate(content.splitlines(), start=1):
            if line.startswith("+") and not line.startswith("+++"):
                self.diff_box.tag_add("add", f"{i}.0", f"{i}.end")
            elif line.startswith("-") and not line.startswith("---"):
                self.diff_box.tag_add("del", f"{i}.0", f"{i}.end")
            elif line.startswith("@@"):
                self.diff_box.tag_add("hunk", f"{i}.0", f"{i}.end")

        self.diff_box.tag_config("add", foreground="#2ECC71")
        self.diff_box.tag_config("del", foreground="#E74C3C")
        self.diff_box.tag_config("hunk", foreground="#3498DB")
        self.diff_box.configure(state="disabled")

    def _confirm(self):
        self.grab_release()
        self.destroy()
        self.on_confirm()

    def _cancel(self):
        self.grab_release()
        self.destroy()
        self.on_cancel()