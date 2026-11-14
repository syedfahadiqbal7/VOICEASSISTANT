# plugins/clipboard_manager.py
from tools.base import BaseTool
from typing import Any, Dict
import subprocess


class ClipboardManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "clipboard_manager"

    @property
    def description(self) -> str:
        return "Get or set Windows clipboard text."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "get or set",
                },
                "text": {
                    "type": "string",
                    "description": "Text to set in clipboard (for set action)",
                },
            },
            "required": ["action"],
        }

    def execute(self, action: str, text: str = "") -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "get":
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True
                )
                return {"content": result.stdout.strip() or "(Clipboard empty)", "is_error": False}

            elif action == "set":
                if not text:
                    return {"content": "text is required for set", "is_error": True}
                process = subprocess.Popen(
                    ["powershell", "-Command", f"Set-Clipboard -Value @'\n{text}\n'@"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                process.communicate()
                return {"content": "Clipboard updated successfully", "is_error": False}

            return {"content": "action must be get or set", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}