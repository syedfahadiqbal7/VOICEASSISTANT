# plugins/json_formatter.py
from tools.base import BaseTool
from typing import Any, Dict
import json


class JSONFormatterTool(BaseTool):
    @property
    def name(self) -> str:
        return "json_formatter"

    @property
    def description(self) -> str:
        return "Format or validate a JSON string."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "json_string": {"type": "string"},
            },
            "required": ["json_string"],
        }

    def execute(self, json_string: str) -> Dict[str, Any]:
        try:
            data = json.loads(json_string)
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
            return {"content": pretty, "is_error": False}
        except Exception as e:
            return {"content": f"Invalid JSON: {e}", "is_error": True}