# plugins/folder_organizer.py
from tools.base import BaseTool
from typing import Any, Dict
import os
import shutil
from pathlib import Path


class FolderOrganizerTool(BaseTool):
    @property
    def name(self) -> str:
        return "folder_organizer"

    @property
    def description(self) -> str:
        return "Organize files in a folder by extension (Documents, Images, Videos, etc.)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "folder_path": {"type": "string", "description": "Folder to organize (default: Downloads)"},
            },
            "required": [],
        }

    def execute(self, folder_path: str = "") -> Dict[str, Any]:
        try:
            if not folder_path:
                folder_path = str(Path.home() / "Downloads")

            categories = {
                "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"],
                "Documents": [".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx"],
                "Videos": [".mp4", ".mkv", ".avi", ".mov", ".wmv"],
                "Audio": [".mp3", ".wav", ".flac", ".aac"],
                "Archives": [".zip", ".rar", ".7z", ".tar", ".gz"],
                "Code": [".py", ".js", ".ts", ".java", ".cpp", ".html", ".css"],
            }

            moved = 0
            for file in os.listdir(folder_path):
                full = os.path.join(folder_path, file)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(file)[1].lower()
                for cat, exts in categories.items():
                    if ext in exts:
                        dest_dir = os.path.join(folder_path, cat)
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.move(full, os.path.join(dest_dir, file))
                        moved += 1
                        break

            return {"content": f"Organized {moved} files in {folder_path}", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}