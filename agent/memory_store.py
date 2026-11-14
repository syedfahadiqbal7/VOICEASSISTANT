# agent/memory_store.py
"""
Phase 1B — Durable useful memory (local JSON).
Stores preferences, frequent paths, recent projects — NOT secrets.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import BASE_DIR

MEMORY_FILE = BASE_DIR / "history" / "agent_memory.json"


class MemoryStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else MEMORY_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {
            "preferences": {},
            "frequent_paths": [],
            "recent_projects": [],
            "facts": [],
            "updated_at": None,
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data.update(raw)
        except Exception as e:
            print(f"[MemoryStore] load failed: {e}")

    def save(self) -> None:
        self._data["updated_at"] = datetime.now().isoformat()
        try:
            from agent.storage import atomic_write_json
            atomic_write_json(self.path, self._data, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[MemoryStore] save failed: {e}")

    # ----- preferences -----
    def set_pref(self, key: str, value: Any) -> None:
        self._data.setdefault("preferences", {})[key] = value
        self.save()

    def get_pref(self, key: str, default: Any = None) -> Any:
        return (self._data.get("preferences") or {}).get(key, default)

    # ----- paths / projects -----
    def remember_path(self, path: str, label: str = "") -> None:
        path = (path or "").replace("\\", "/").strip()
        if not path:
            return
        entries: List[Dict[str, str]] = self._data.setdefault("frequent_paths", [])
        entries = [e for e in entries if e.get("path") != path]
        entries.insert(
            0,
            {
                "path": path,
                "label": label or os.path.basename(path),
                "last_used": datetime.now().isoformat(),
            },
        )
        self._data["frequent_paths"] = entries[:20]
        self.save()

    def remember_project(self, name: str, path: str = "") -> None:
        name = (name or "").strip()
        if not name:
            return
        projects: List[Dict[str, str]] = self._data.setdefault("recent_projects", [])
        projects = [p for p in projects if p.get("name") != name]
        projects.insert(
            0,
            {
                "name": name,
                "path": (path or "").replace("\\", "/"),
                "last_used": datetime.now().isoformat(),
            },
        )
        self._data["recent_projects"] = projects[:15]
        self.save()

    def add_fact(self, fact: str) -> None:
        fact = (fact or "").strip()
        if not fact:
            return
        facts: List[str] = self._data.setdefault("facts", [])
        if fact not in facts:
            facts.insert(0, fact)
        self._data["facts"] = facts[:50]
        self.save()

    def context_snippet(self, max_chars: int = 600) -> str:
        """Short block to optionally inject into system context."""
        lines = []
        prefs = self._data.get("preferences") or {}
        if prefs:
            lines.append("User preferences: " + ", ".join(f"{k}={v}" for k, v in list(prefs.items())[:8]))
        paths = self._data.get("frequent_paths") or []
        if paths:
            lines.append(
                "Frequent paths: "
                + "; ".join(e.get("path", "") for e in paths[:5])
            )
        projects = self._data.get("recent_projects") or []
        if projects:
            lines.append(
                "Recent projects: "
                + ", ".join(p.get("name", "") for p in projects[:5])
            )
        facts = self._data.get("facts") or []
        if facts:
            lines.append("Notes: " + " | ".join(facts[:5]))
        text = "\n".join(lines).strip()
        return text[:max_chars]

    def observe_tool_success(self, tool_name: str, args: Dict[str, Any], result_content: str = "") -> None:
        """Learn from successful actions."""
        if tool_name == "write_developer_code":
            fp = (args or {}).get("filepath") or ""
            if fp:
                self.remember_path(fp)
                # project-ish folder name
                parts = fp.replace("\\", "/").split("/")
                if "Desktop" in parts:
                    idx = parts.index("Desktop")
                    if idx + 1 < len(parts) - 1:
                        self.remember_project(parts[idx + 1], "/".join(parts[: idx + 2]))
        if tool_name == "open_app":
            target = (args or {}).get("target") or ""
            if target:
                self.add_fact(f"Often opens app: {target}")
