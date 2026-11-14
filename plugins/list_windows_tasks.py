# plugins/list_windows_tasks.py
"""
Windows Task Scheduler viewer (schtasks / Get-ScheduledTask).

This is NOT the agent JobScheduler.
Agent schedules use: Schedules button, or text like
  schedule in 2 minutes: Open notepad
"""
from tools.base import BaseTool
from typing import Any, Dict
import subprocess


class ListWindowsTasksTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_windows_tasks"

    @property
    def description(self) -> str:
        return (
            "List Windows Task Scheduler tasks (Get-ScheduledTask). "
            "NOT the agent JobScheduler — for agent jobs use Schedules UI "
            "or 'schedule in X minutes: ...' / 'list schedules'."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional TaskName filter (substring match)",
                },
            },
            "required": [],
        }

    def execute(self, filter: str = "") -> Dict[str, Any]:
        try:
            if filter:
                cmd = (
                    "Get-ScheduledTask | "
                    f"Where-Object {{$_.TaskName -like '*{filter}*'}} | "
                    "Select-Object TaskName, State, TaskPath | "
                    "Format-Table -AutoSize"
                )
            else:
                cmd = (
                    "Get-ScheduledTask | Select-Object -First 25 "
                    "TaskName, State, TaskPath | Format-Table -AutoSize"
                )
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=20,
            )
            out = (result.stdout or "").strip() or "No tasks found"
            if result.returncode != 0 and result.stderr:
                out = (result.stderr or "").strip() or out
            return {"content": out, "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}
