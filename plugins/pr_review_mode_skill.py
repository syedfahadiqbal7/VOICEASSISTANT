# plugins/pr_review_mode_skill.py
"""Auto-generated custom skill: PR Review Mode
DO NOT edit by hand unless you know what you are doing.
Regenerate from Skill Author UI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.base import BaseTool

# Fixed plan baked at generation time
_ACTIONS: List[Dict[str, Any]] = [
  {
    "tool": "browser_open",
    "url": "https://github.com/pulls"
  },
  {
    "tool": "open_app",
    "target": "Code"
  }
]


class PrReviewModeSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_pr_review_mode"

    @property
    def description(self) -> str:
        return (
            "Open GitHub PRs and VS Code for review "
            "Triggers: pr review mode, review pull requests. "
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
        lines = ["Skill 'pr_review_mode' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
