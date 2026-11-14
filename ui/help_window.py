# ui/help_window.py
import customtkinter as ctk
from pathlib import Path
from typing import List, Dict, Optional


class HelpWindow(ctk.CTkToplevel):
    def __init__(self, parent, tool_help_list: Optional[List[Dict]] = None):
        super().__init__(parent)
        self.title("Help Guide - Agentic Voice Assistant")
        self.geometry("950x700")
        self.minsize(800, 550)
        self.transient(parent)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Create text area FIRST
        self.text_area = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.text_area.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")

        # Load main HELP.md content
        help_path = Path(__file__).parent.parent / "HELP.md"
        if help_path.exists():
            content = help_path.read_text(encoding="utf-8")
        else:
            content = "# Help Guide\n\nHELP.md file not found.\n"

        # Append dynamic plugin help
        if tool_help_list:
            content += "\n\n---\n\n## Installed Plugins Help\n\n"
            for tool in tool_help_list:
                content += f"### `{tool.get('name', 'unknown')}`\n\n"
                content += f"**Summary:** {tool.get('summary', 'No summary')}\n\n"
                content += f"**Usage:**\n{tool.get('usage', 'No usage info')}\n\n"
                content += f"**Examples:**\n{tool.get('examples', 'No examples')}\n\n"
                content += "---\n\n"

        # Now insert content
        self.text_area.insert("0.0", content)
        self.text_area.configure(state="disabled")

        # Close button
        close_btn = ctk.CTkButton(
            self,
            text="Close",
            width=100,
            command=self.destroy
        )
        close_btn.grid(row=1, column=0, pady=(0, 15))