# plugins/docker_manager.py
from tools.base import BaseTool
from typing import Any, Dict
import subprocess


class DockerManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "docker_manager"

    @property
    def description(self) -> str:
        return "Manage Docker: list containers, start, stop, restart, logs."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "ps, start, stop, restart, logs",
                },
                "container": {
                    "type": "string",
                    "description": "Container name or ID",
                },
            },
            "required": ["action"],
        }

    def execute(self, action: str, container: str = "") -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "ps":
                cmd = ["docker", "ps", "-a"]
            elif action in ("start", "stop", "restart"):
                if not container:
                    return {"content": "container name/ID required", "is_error": True}
                cmd = ["docker", action, container]
            elif action == "logs":
                if not container:
                    return {"content": "container name/ID required", "is_error": True}
                cmd = ["docker", "logs", "--tail", "50", container]
            else:
                return {"content": f"Unknown action: {action}", "is_error": True}

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            output = result.stdout.strip() or result.stderr.strip() or "Done"
            return {"content": output, "is_error": result.returncode != 0}
        except FileNotFoundError:
            return {"content": "Docker is not installed or not in PATH", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}