# agent/skill_packs.py
"""
Phase 3 — Skill packs (Browser, Office, VS Code).

Each pack groups related tools + outcome phrases. Enable/disable is stored in
history/skill_packs.json. Tools still load from plugins/; packs gate outcomes
and optional soft-deny of pack tools when disabled.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from agent.skill_author import load_all_custom_manifests
from agent.storage import atomic_write_json, load_json
from agent.data_protection import DataProtectionError

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = Path(".").resolve()

PACKS_FILE = Path(BASE_DIR) / "history" / "skill_packs.json"

PACK_CATALOG: Dict[str, Dict[str, Any]] = {
    "browser": {
        "title": "Browser",
        "description": "Open URLs, web search, YouTube play",
        "tools": ["browser_open", "web_search_open", "web_search_inapp", "youtube_player"],
        "default_enabled": True,
    },
    "office": {
        "title": "Office",
        "description": "Open Word/Excel/PowerPoint and create simple office files",
        "tools": ["office_open", "office_new"],
        "default_enabled": True,
    },
    "vscode": {
        "title": "VS Code",
        "description": "Open files/folders in Visual Studio Code",
        "tools": ["vscode_open"],
        "default_enabled": True,
    },
    "forjob": {
        "title": "ForJob Application Agent",
        "description": "JD match, tracker log, apply checklist, daily plan (no auto-submit)",
        "tools": [
            "forjob_score_jd",
            "forjob_draft_tracker_row",
            "forjob_apply_checklist",
            "forjob_daily_plan",
            "forjob_ats_check",
            "forjob_create_tracker",
            "read_document",
            "write_docx",
            "write_pdf",
            "github_list_repos",
        ],
        "default_enabled": True,
    },
}


class SkillPackManager:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else PACKS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled: Dict[str, bool] = {
            k: bool(v.get("default_enabled", True)) for k, v in PACK_CATALOG.items()
        }
        self.load()
        self._merge_custom_packs()

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        raw = load_json(self.path, default=None, encrypt=True)
        if raw is not None:
            if not isinstance(raw, dict):
                raise ValueError("skill pack settings must be a JSON object")
            if isinstance(raw.get("enabled"), dict):
                for k, v in raw["enabled"].items():
                    if k in self.enabled:
                        self.enabled[k] = bool(v)

    def save(self) -> None:
        atomic_write_json(
            self.path, {"version": 1, "enabled": self.enabled},
            indent=2, encrypt=True,
        )

    def is_enabled(self, pack_id: str) -> bool:
        return bool(self.enabled.get(pack_id, False))

    def set_enabled(self, pack_id: str, on: bool) -> bool:
        if pack_id not in PACK_CATALOG:
            return False
        self.enabled[pack_id] = bool(on)
        self.save()
        return True

    def list_packs(self) -> List[Dict[str, Any]]:
        out = []
        for pid, meta in PACK_CATALOG.items():
            out.append(
                {
                    "id": pid,
                    "title": meta["title"],
                    "description": meta["description"],
                    "tools": list(meta.get("tools") or []),
                    "enabled": self.is_enabled(pid),
                }
            )
        return out

    def summary_text(self) -> str:
        lines = ["Skill packs:"]
        for p in self.list_packs():
            flag = "ON" if p["enabled"] else "OFF"
            lines.append(f"  [{flag}] {p['id']}: {p['title']} — {p['description']}")
        lines.append("Say: enable skill office | disable skill browser")
        return "\n".join(lines)

    def tools_for_disabled_packs(self) -> List[str]:
        denied = []
        for pid, meta in PACK_CATALOG.items():
            if not self.is_enabled(pid):
                denied.extend(meta.get("tools") or [])
        return denied

    def _merge_custom_packs(self) -> None:
        """Register custom skills from history/custom_skills/*.json into catalog."""
        try:
            for man in load_all_custom_manifests():
                sid = man.get("skill_id")
                if not sid:
                    continue
                PACK_CATALOG[sid] = {
                    "title": man.get("title") or sid,
                    "description": man.get("description") or "Custom skill",
                    "tools": list(man.get("tools") or []),
                    "default_enabled": bool(man.get("enabled", True)),
                    "custom": True,
                }
                if sid not in self.enabled:
                    self.enabled[sid] = bool(man.get("enabled", True))
        except DataProtectionError:
            raise
        except Exception as e:
            print(f"[SkillPacks] custom merge failed: {e}")

    def reload_custom(self) -> None:
        self._merge_custom_packs()
        self.save()
