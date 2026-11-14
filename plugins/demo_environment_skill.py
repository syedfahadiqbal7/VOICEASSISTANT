# plugins/demo_environment_skill.py
"""Auto-generated custom skill: Demo Environment
DO NOT edit by hand unless you know what you are doing.
Regenerate from Skill Author UI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.base import BaseTool

# Fixed plan baked at generation time
_ACTIONS: List[Dict[str, Any]] = [
  {
    "tool": "open_app",
    "target": "chrome"
  },
  {
    "tool": "browser_open",
    "url": "https://github.com"
  },
  {
    "tool": "office_new",
    "kind": "csv",
    "name": "demo_script",
    "open_after": "true"
  }
]


class DemoEnvironmentSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_demo_environment"

    @property
    def description(self) -> str:
        return (
            "Browser, GitHub, and demo script sheet "
            "Triggers: demo environment, prepare demo. "
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
        lines = ["Skill 'demo_environment' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
