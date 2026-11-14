# plugins/env_manager.py
from tools.base import BaseTool
from typing import Any, Dict
import os
import subprocess


class EnvManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "env_manager"

    @property
    def description(self) -> str:
        return "Get or set environment variables."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "get or set"},
                "name": {"type": "string"},
                "value": {"type": "string", "description": "Value for set action"},
            },
            "required": ["action", "name"],
        }

    def execute(self, action: str, name: str, value: str = "") -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "get":
                val = os.environ.get(name)
                return {"content": f"{name} = {val}" if val is not None else f"{name} is not set", "is_error": False}
            elif action == "set":
                # Set for current process + user permanently
                os.environ[name] = value
                subprocess.run(["setx", name, value], capture_output=True)
                return {"content": f"Set {name} = {value}", "is_error": False}
            return {"content": "action must be get or set", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}