# plugins/security_baseline_skill.py
"""Auto-generated custom skill: Security Baseline
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
    "url": "https://learn.microsoft.com/security/benchmark"
  },
  {
    "tool": "web_search_open",
    "query": "cis benchmarks cloud",
    "engine": "google"
  }
]


class SecurityBaselineSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_security_baseline"

    @property
    def description(self) -> str:
        return (
            "search the web for cis benchmarks cloud "
            "Triggers: security baseline. "
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
        lines = ["Skill 'security_baseline' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
