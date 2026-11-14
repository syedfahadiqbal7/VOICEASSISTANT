# plugins/release_notes_skill.py
"""Auto-generated custom skill: Release Notes
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
    "name": "release_notes",
    "open_after": "true"
  },
  {
    "tool": "open_app",
    "target": "notepad"
  },
  {
    "tool": "web_search_open",
    "query": "software release notes examples",
    "engine": "google"
  }
]


class ReleaseNotesSkillTool(BaseTool):
    @property
    def name(self) -> str:
        return "run_release_notes"

    @property
    def description(self) -> str:
        return (
            "Release notes sheet, notepad, examples "
            "Triggers: release notes, draft release notes. "
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
        lines = ["Skill 'release_notes' actions:"]
        for a in _ACTIONS:
            lines.append(f"  - {a.get('tool')}: { {k:v for k,v in a.items() if k!='tool'} }")
        return {"content": "\n".join(lines), "is_error": False}
