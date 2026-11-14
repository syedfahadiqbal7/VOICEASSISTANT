# plugins/vscode_skill.py
"""VS Code skill pack: open file or folder in Code / Cursor."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.base import BaseTool


class VSCodeOpenTool(BaseTool):
    @property
    def name(self) -> str:
        return "vscode_open"

    @property
    def description(self) -> str:
        return (
            "Open a file or folder in Visual Studio Code (or Cursor if code CLI missing). "
            "Pass path to file/folder. Use when user says open in VS Code / code ."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or folder path. Default Desktop if empty.",
                },
                "editor": {
                    "type": "string",
                    "description": "code (default) or cursor",
                    "enum": ["code", "cursor"],
                },
            },
            "required": [],
        }

    def _find_cli(self, editor: str) -> Optional[str]:
        names = ["code", "code.cmd"] if editor == "code" else ["cursor", "cursor.cmd"]
        for n in names:
            w = shutil.which(n)
            if w:
                return w
        # common install locations
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates: List[str] = []
        if editor == "code":
            candidates = [
                str(Path(local) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"),
                str(Path(pf) / "Microsoft VS Code" / "bin" / "code.cmd"),
            ]
        else:
            candidates = [
                str(Path(local) / "Programs" / "cursor" / "Cursor.exe"),
                str(Path(local) / "Programs" / "Cursor" / "Cursor.exe"),
            ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    def execute(self, path: str = "", editor: str = "code", **kwargs) -> Dict[str, Any]:
        editor = (editor or "code").lower()
        path = (path or "").strip().strip('"')
        if not path:
            path = str(Path.home() / "Desktop")
        # expand desktop/
        if path.lower().startswith("desktop/") or path.lower() == "desktop":
            rest = path.split("/", 1)[-1] if "/" in path else ""
            path = str(Path.home() / "Desktop" / rest) if rest else str(Path.home() / "Desktop")

        cli = self._find_cli(editor)
        if not cli and editor == "code":
            cli = self._find_cli("cursor")
            editor = "cursor" if cli else editor
        if not cli:
            # last resort startfile on folder
            try:
                if os.path.exists(path):
                    os.startfile(path)  # type: ignore
                    return {
                        "content": f"Opened path with shell (no code CLI): {path}",
                        "is_error": False,
                    }
            except Exception as e:
                return {"content": f"VS Code CLI not found and open failed: {e}", "is_error": True}
            return {
                "content": "VS Code CLI (code) not found. Install VS Code and enable 'code' in PATH.",
                "is_error": True,
            }

        try:
            # Windows: code.cmd path
            subprocess.Popen(
                [cli, path],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"content": f"Opened in {editor}: {path}", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}
