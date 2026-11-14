# plugins/competitor_scan_skill.py
"""Auto-generated custom skill: Competitor Scan
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
    "query": "competitor analysis template SaaS",
    "engine": "google"
  },
  {
    "tool": "browser_open",
    "url": "https://www.g2.com"
  }
]


class CompetitorScanSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_competitor_scan"

    @property
    def description(self) -> str:
        return (
            "Custom skill generated from text: Competitor Scan "
            "Triggers: competitor scan. "
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
        lines = ["Skill 'competitor_scan' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
