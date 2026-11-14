# plugins/file_reader.py
from tools.base import BaseTool
from typing import Any, Dict
import os
import re


class FileReaderTool(BaseTool):
    @property
    def name(self) -> str:
        return "file_reader"

    @property
    def description(self) -> str:
        return "Read a file content or search for text inside files in a directory."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "read or search",
                },
                "path": {
                    "type": "string",
                    "description": "File path (for read) or directory path (for search)",
                },
                "query": {
                    "type": "string",
                    "description": "Text to search (required for search)",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Max lines to return when reading (default 200)",
                },
            },
            "required": ["action", "path"],
        }

    def execute(self, action: str, path: str, query: str = "", max_lines: int = 200) -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "read":
                if not os.path.isfile(path):
                    return {"content": f"File not found: {path}", "is_error": True}
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                content = "".join(lines[:max_lines])
                if len(lines) > max_lines:
                    content += f"\n\n... (truncated, total {len(lines)} lines)"
                return {"content": content, "is_error": False}

            elif action == "search":
                if not query:
                    return {"content": "query is required for search", "is_error": True}
                results = []
                for root, _, files in os.walk(path):
                    for file in files:
                        if file.endswith((".py", ".js", ".ts", ".java", ".cpp", ".c", ".h", ".cs", ".go", ".rs", ".txt", ".md", ".json", ".yaml", ".yml")):
                            full = os.path.join(root, file)
                            try:
                                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                                    for i, line in enumerate(f, 1):
                                        if query.lower() in line.lower():
                                            results.append(f"{full}:{i}: {line.strip()}")
                                            if len(results) >= 30:
                                                break
                            except Exception:
                                continue
                    if len(results) >= 30:
                        break
                if not results:
                    return {"content": "No matches found.", "is_error": False}
                return {"content": "\n".join(results), "is_error": False}

            return {"content": "action must be 'read' or 'search'", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}