# plugins/sales_call_prep_skill.py
"""Auto-generated custom skill: Sales Call Prep
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
    "query": "BANT sales qualification questions",
    "engine": "google"
  },
  {
    "tool": "office_new",
    "kind": "csv",
    "name": "call_notes",
    "open_after": "true"
  }
]


class SalesCallPrepSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_sales_call_prep"

    @property
    def description(self) -> str:
        return (
            "create a csv named call_notes "
            "Triggers: sales call prep. "
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
        lines = ["Skill 'sales_call_prep' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
