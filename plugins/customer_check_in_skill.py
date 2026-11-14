# plugins/customer_check_in_skill.py
"""Auto-generated custom skill: Customer Check In
DO NOT edit by hand unless you know what you are doing.
Regenerate from Skill Author UI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.base import BaseTool

# Fixed plan baked at generation time
_ACTIONS: List[Dict[str, Any]] = [
  {
    "tool": "web_search_open",
    "query": "customer success QBR agenda template",
    "engine": "google"
  },
  {
    "tool": "office_new",
    "kind": "csv",
    "name": "qbr_notes",
    "open_after": "true"
  }
]


class CustomerCheckInSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_customer_check_in"

    @property
    def description(self) -> str:
        return (
            "QBR agenda search and notes sheet "
            "Triggers: customer check in, qbr prep. "
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
        lines = ["Skill 'customer_check_in' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
