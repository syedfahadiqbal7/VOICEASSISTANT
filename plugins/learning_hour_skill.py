# plugins/learning_hour_skill.py
"""Auto-generated custom skill: Learning Hour
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
    "query": "system design primer",
    "engine": "google"
  },
  {
    "tool": "browser_open",
    "url": "https://www.youtube.com"
  }
]


class LearningHourSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_learning_hour"

    @property
    def description(self) -> str:
        return (
            "System design learning and YouTube "
            "Triggers: learning hour, study time. "
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
        lines = ["Skill 'learning_hour' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
