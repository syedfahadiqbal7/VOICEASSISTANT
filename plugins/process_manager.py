# plugins/process_manager.py
from tools.base import BaseTool
from typing import Any, Dict
import psutil


class ProcessManagerTool(BaseTool):
    @property
    def name(self) -> str:
        return "process_manager"

    @property
    def description(self) -> str:
        return "List top processes or kill a process by name or PID."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "list or kill",
                },
                "name": {
                    "type": "string",
                    "description": "Process name (for kill)",
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID (for kill)",
                },
            },
            "required": ["action"],
        }

    def execute(self, action: str, name: str = "", pid: int = 0) -> Dict[str, Any]:
        try:
            action = action.lower()
            if action == "list":
                procs = []
                for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                    try:
                        info = p.info
                        procs.append(info)
                    except Exception:
                        continue
                procs = sorted(procs, key=lambda x: x.get("cpu_percent") or 0, reverse=True)[:15]
                lines = [f"PID: {p['pid']:<6} CPU: {p['cpu_percent'] or 0:>5.1f}%  MEM: {p['memory_percent'] or 0:>5.1f}%  {p['name']}" for p in procs]
                return {"content": "\n".join(lines), "is_error": False}

            elif action == "kill":
                killed = []
                if pid:
                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        killed.append(f"PID {pid}")
                    except Exception as e:
                        return {"content": str(e), "is_error": True}
                elif name:
                    for p in psutil.process_iter(["pid", "name"]):
                        if name.lower() in (p.info["name"] or "").lower():
                            try:
                                p.terminate()
                                killed.append(f"{p.info['name']} ({p.info['pid']})")
                            except Exception:
                                continue
                else:
                    return {"content": "Provide name or pid to kill", "is_error": True}

                if killed:
                    return {"content": "Terminated: " + ", ".join(killed), "is_error": False}
                return {"content": "No matching process found", "is_error": True}

            return {"content": "action must be list or kill", "is_error": True}
        except Exception as e:
            return {"content": str(e), "is_error": True}