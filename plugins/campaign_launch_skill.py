# plugins/campaign_launch_skill.py
"""Auto-generated custom skill: Campaign Launch
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
    "query": "go to market campaign launch checklist",
    "engine": "google"
  },
  {
    "tool": "office_new",
    "kind": "csv",
    "name": "campaign_checklist",
    "open_after": "true"
  }
]


class CampaignLaunchSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_campaign_launch"

    @property
    def description(self) -> str:
        return (
            "GTM checklist search and local checklist CSV "
            "Triggers: campaign launch, launch campaign. "
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
        lines = ["Skill 'campaign_launch' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
