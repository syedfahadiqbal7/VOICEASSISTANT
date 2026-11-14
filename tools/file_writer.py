# tools/file_writer.py
"""Write files or create folders on disk."""

from __future__ import annotations

import os
from typing import Any, Dict

from tools.base import BaseTool


class FileWriterTool(BaseTool):
    @property
    def name(self) -> str:
        return "write_developer_code"

    @property
    def description(self) -> str:
        return (
            "Create or overwrite a text file at filepath with the given content. "
            "Parent folders are created automatically. "
            "To create a folder only: set filepath to the folder path ending with / "
            "or without a file extension and content empty or '.keep'. "
            "Use the exact filename and extension the user requested "
            "(e.g. if user says abc.txt then filepath must end with abc.txt). "
            "Never create .bat or .cmd files unless the user explicitly asks for a batch file."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": (
                        "Full path for a file (e.g. C:/Users/You/Desktop/test/abc.txt) "
                        "or a folder (e.g. C:/Users/You/Desktop/ForJob/ or Desktop/ForJob). "
                        "Desktop/relative paths are expanded."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "File content. For folder-only create use empty string or '.keep'."
                    ),
                },
            },
            "required": ["filepath"],
        }

    def sanitize_path(self, filepath: str) -> str:
        filepath = (
            (filepath or "")
            .replace("<", "")
            .replace(">", "")
            .replace('"', "")
            .replace("'", "")
        )
        home_dir = os.path.expanduser("~").replace("\\", "/")
        placeholders = [
            "YourUsername",
            "YourName",
            "yourusername",
            "username",
            "YourUserName",
        ]
        for p in placeholders:
            if p in filepath:
                filepath = filepath.replace(f"C:/Users/{p}", home_dir).replace(
                    p, os.path.basename(home_dir)
                )
        # Desktop-relative
        low = filepath.replace("\\", "/")
        if low.startswith("Desktop/") or low.startswith("desktop/"):
            filepath = f"{home_dir}/Desktop/{filepath.replace(chr(92), '/').split('/', 1)[-1]}"
        elif low.lower() in ("desktop", "desktop/"):
            filepath = f"{home_dir}/Desktop"
        return filepath.replace("\\", "/")

    def _is_directory_request(self, filepath: str, content: str) -> bool:
        fp = (filepath or "").rstrip()
        c = (content or "").strip()
        if fp.endswith("/") or fp.endswith("\\"):
            return True
        # no extension and placeholder content
        base = os.path.basename(fp.rstrip("/\\"))
        _, ext = os.path.splitext(base)
        if not ext and c in ("", ".keep", "keep"):
            return True
        # explicit .keep file inside folder still a file — only pure dir if no ext
        return False

    def execute(self, filepath: str = "", content: str = "", **kwargs) -> Dict[str, Any]:
        try:
            filepath = self.sanitize_path(filepath)
            if not filepath:
                return {"content": "filepath is required", "is_error": True}

            content = content if content is not None else ""

            # Folder-only create
            if self._is_directory_request(filepath, content):
                dir_path = filepath.rstrip("/\\")
                os.makedirs(dir_path, exist_ok=True)
                return {
                    "content": f"Folder ready: {dir_path}",
                    "is_error": False,
                }

            dir_name = os.path.dirname(filepath)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "content": f"File deployed successfully to {filepath}",
                "is_error": False,
            }
        except Exception as e:
            return {
                "content": f"File writing failed: {e}",
                "is_error": True,
            }
