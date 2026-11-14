# plugins/debug_logs_skill.py
"""Auto-generated custom skill: Debug Logs
DO NOT edit by hand unless you know what you are doing.
Regenerate from Skill Author UI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.base import BaseTool

# Fixed plan baked at generation time
_ACTIONS: List[Dict[str, Any]] = [
  {
    "tool": "office_new",
    "kind": "csv",
    "name": "debug_log",
    "open_after": "true"
  },
  {
    "tool": "open_app",
    "target": "notepad"
  }
]


class DebugLogsSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_debug_logs"

    @property
    def description(self) -> str:
        return (
            "create a csv named debug_log "
            "Triggers: debug logs. "
            "Runs a fixed multi-step skill plan."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        # Execution is normally done via OutcomeCompiler plan steps.
        # This tool is a single-shot fallback that only reports the plan.
        lines = ["Skill 'debug_logs' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
