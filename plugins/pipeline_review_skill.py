# plugins/pipeline_review_skill.py
"""Auto-generated custom skill: Pipeline Review
DO NOT edit by hand unless you know what you are doing.
Regenerate from Skill Author UI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.base import BaseTool

# Fixed plan baked at generation time
_ACTIONS: List[Dict[str, Any]] = [
  {
    "tool": "office_open",
    "app": "excel"
  },
  {
    "tool": "office_new",
    "kind": "csv",
    "name": "sales_pipeline",
    "open_after": "true"
  }
]


class PipelineReviewSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_pipeline_review"

    @property
    def description(self) -> str:
        return (
            "Excel and sales pipeline CSV "
            "Triggers: pipeline review, review pipeline. "
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
        lines = ["Skill 'pipeline_review' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
