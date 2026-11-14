# plugins/voice_note_saver.py
from tools.base import BaseTool
from typing import Any, Dict
from datetime import datetime
from pathlib import Path


class VoiceNoteSaverTool(BaseTool):
    @property
    def name(self) -> str:
        return "voice_note_saver"

    @property
    def description(self) -> str:
        return "Save a text note (usually coming from voice) to a notes file on Desktop."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The text content of the note"},
                "filename": {"type": "string", "description": "Optional filename"},
            },
            "required": ["note"],
        }

    def execute(self, note: str, filename: str = "") -> Dict[str, Any]:
        try:
            desktop = Path.home() / "Desktop"
            if not filename:
                filename = f"voice_note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            if not filename.endswith(".txt"):
                filename += ".txt"

            path = desktop / filename
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n{note}\n\n")

            return {"content": f"Note saved to {path}", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}