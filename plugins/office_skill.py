# plugins/office_skill.py
"""Office skill pack: open Word/Excel/PowerPoint; create simple CSV/HTML docs."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Dict, List

from tools.base import BaseTool

# Reuse open_app resolver if available
try:
    from plugins.open_app import OpenAppTool
except Exception:
    try:
        import importlib.util
        # fallback relative load handled at execute via open_app tool registry
        OpenAppTool = None  # type: ignore
    except Exception:
        OpenAppTool = None  # type: ignore


class OfficeOpenTool(BaseTool):
    APP_HINTS = {
        "word": "winword.exe",
        "winword": "winword.exe",
        "excel": "excel.exe",
        "powerpoint": "powerpnt.exe",
        "ppt": "powerpnt.exe",
        "onenote": "onenote.exe",
        "outlook": "outlook.exe",
    }

    @property
    def name(self) -> str:
        return "office_open"

    @property
    def description(self) -> str:
        return (
            "Open Microsoft Office apps (Word, Excel, PowerPoint, Outlook, OneNote) "
            "or open a document path in the associated Office app."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "word | excel | powerpoint | outlook | onenote",
                },
                "path": {
                    "type": "string",
                    "description": "Optional file path to open (.docx, .xlsx, .pptx)",
                },
            },
            "required": [],
        }

    def execute(self, app: str = "", path: str = "", **kwargs) -> Dict[str, Any]:
        path = (path or "").strip().strip('"')
        app = (app or "").strip().lower()
        try:
            if path and os.path.isfile(path):
                os.startfile(path)  # type: ignore
                return {"content": f"Opened Office file: {path}", "is_error": False}

            target = self.APP_HINTS.get(app) or app or "excel.exe"
            # Prefer OpenAppTool resolution
            if OpenAppTool is not None:
                tool = OpenAppTool()
                return tool.execute(target=target)
            os.startfile(target)  # type: ignore
            return {"content": f"Launched: {target}", "is_error": False}
        except Exception as e:
            return {
                "content": f"Could not open Office app/file ({app or path}): {e}",
                "is_error": True,
            }


class OfficeNewTool(BaseTool):
    @property
    def name(self) -> str:
        return "office_new"

    @property
    def description(self) -> str:
        return (
            "Create a simple spreadsheet (CSV) or HTML document on the Desktop "
            "and optionally open it. Use for quick tables/notes without full OOXML."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["csv", "html", "txt"],
                    "description": "csv (Excel-friendly), html, or txt",
                },
                "name": {
                    "type": "string",
                    "description": "File name without path, e.g. report.csv",
                },
                "content": {
                    "type": "string",
                    "description": "Text content. For csv use lines of comma-separated values.",
                },
                "open_after": {
                    "type": "boolean",
                    "description": "Open file after create (default true)",
                },
            },
            "required": ["kind", "name"],
        }

    def execute(
        self,
        kind: str = "csv",
        name: str = "sheet.csv",
        content: str = "",
        open_after: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        kind = (kind or "csv").lower()
        name = (name or "sheet.csv").strip()
        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        # ensure extension
        ext = {"csv": ".csv", "html": ".html", "txt": ".txt"}.get(kind, ".txt")
        if not name.lower().endswith(ext):
            name = name + ext
        path = desktop / name
        try:
            body = content or ""
            if kind == "csv" and not body:
                body = "Column1,Column2\nvalue1,value2\n"
            if kind == "html" and not body:
                body = f"<html><body><h1>{name}</h1><p>Created by VoiceAssistant.</p></body></html>"
            path.write_text(body, encoding="utf-8")
            if open_after:
                try:
                    os.startfile(str(path))  # type: ignore
                except Exception:
                    pass
            return {
                "content": f"Created Office-friendly file: {path}",
                "is_error": False,
            }
        except Exception as e:
            return {"content": str(e), "is_error": True}
