# plugins/network_tools.py
from tools.base import BaseTool
from typing import Any, Dict
import subprocess
import socket


class NetworkToolsTool(BaseTool):
    @property
    def name(self) -> str:
        return "network_tools"

    @property
    def description(self) -> str:
        return "Network utilities: ip, ping, port check."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "ip, ping, port_check",
                },
                "host": {
                    "type": "string",
                    "description": "Hostname or IP (for ping / port_check)",
                },
                "port": {
                    "type": "integer",
                    "description": "Port number (for port_check)",
                },
            },
            "required": ["action"],
        }

    def execute(self, action: str, host: str = "", port: int = 80) -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "ip":
                result = subprocess.run(["ipconfig"], capture_output=True, text=True)
                return {"content": result.stdout[:2000], "is_error": False}

            elif action == "ping":
                if not host:
                    return {"content": "host is required", "is_error": True}
                result = subprocess.run(["ping", "-n", "4", host], capture_output=True, text=True, timeout=15)
                return {"content": result.stdout, "is_error": result.returncode != 0}

            elif action == "port_check":
                if not host:
                    return {"content": "host is required", "is_error": True}
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((host, port))
                sock.close()
                status = "OPEN" if result == 0 else "CLOSED"
                return {"content": f"Port {port} on {host} is {status}", "is_error": False}

            return {"content": "Unknown action", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}