# plugins/port_killer.py
from tools.base import BaseTool
from typing import Any, Dict
import subprocess


class PortKillerTool(BaseTool):
    @property
    def name(self) -> str:
        return "port_killer"

    @property
    def description(self) -> str:
        return "Find and kill the process that is using a specific port."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "port": {"type": "integer"},
            },
            "required": ["port"],
        }

    def execute(self, port: int) -> Dict[str, Any]:
        try:
            # Find PID
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True
            )
            pids = set()
            for line in result.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pids.add(parts[-1])

            if not pids:
                return {"content": f"No process found listening on port {port}", "is_error": False}

            killed = []
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                killed.append(pid)

            return {"content": f"Killed PIDs on port {port}: {', '.join(killed)}", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}