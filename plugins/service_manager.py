# plugins/service_manager.py
from tools.base import BaseTool
from typing import Any, Dict
import subprocess


class ServiceManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "service_manager"

    @property
    def description(self) -> str:
        return "Manage Windows services: list, start, stop, restart."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list, start, stop, restart",
                },
                "service_name": {
                    "type": "string",
                    "description": "Name of the Windows service",
                },
            },
            "required": ["action"],
        }

    def execute(self, action: str, service_name: str = "") -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "list":
                cmd = ["powershell", "-Command", "Get-Service | Select-Object -First 30 Name, Status, DisplayName | Format-Table -AutoSize"]
            elif action in ("start", "stop", "restart"):
                if not service_name:
                    return {"content": "service_name is required", "is_error": True}
                cmd = ["powershell", "-Command", f"{action.title()}-Service -Name '{service_name}'"]
            else:
                return {"content": "Invalid action", "is_error": True}

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout.strip() or result.stderr.strip() or "Done"
            return {"content": output, "is_error": result.returncode != 0}
        except Exception as e:
            return {"content": str(e), "is_error": True}