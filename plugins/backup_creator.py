# plugins/backup_creator.py
from tools.base import BaseTool
from typing import Any, Dict
import os
import shutil
from datetime import datetime
from pathlib import Path


class BackupCreatorTool(BaseTool):
    @property
    def name(self) -> str:
        return "backup_creator"

    @property
    def description(self) -> str:
        return "Create a zip backup of a folder."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source_folder": {"type": "string"},
                "backup_name": {"type": "string", "description": "Optional custom name"},
            },
            "required": ["source_folder"],
        }

    def execute(self, source_folder: str, backup_name: str = "") -> Dict[str, Any]:
        try:
            if not os.path.isdir(source_folder):
                return {"content": "Source folder does not exist", "is_error": True}

            desktop = Path.home() / "Desktop"
            name = backup_name or f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            dest = desktop / name

            shutil.make_archive(str(dest), "zip", source_folder)
            return {"content": f"Backup created: {dest}.zip", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}