# agent/planner.py
"""
Phase 1 — Multi-step Planner
Turns a user goal into a strict JSON plan of tool steps with success checks.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from agent.llm_client import LLMClient


PLAN_SYSTEM = """You are a planning module for a Windows desktop AI agent.
Return ONLY valid JSON. No markdown fences. No extra text.

Rules:
1. Use ONLY tools from the provided list.
2. Each step: id, title, tool, arguments, success_check, risk
3. success_check.type: file_exists | file_contains | dir_exists | process_running | always_true | command_exit_zero
4. Max 6 steps. Prefer 1 step when possible.
5. For folder+file creation use ONE write_developer_code step (parents auto-created).
6. write_developer_code content = REAL file text only. Never "echo x > file".
7. risk: low | medium | high
8. needs_confirmation: true if medium/high risk or 2+ mutating steps

JSON only:
{"goal":"...","needs_confirmation":false,"steps":[{"id":1,"title":"...","tool":"...","arguments":{},"success_check":{"type":"file_exists","path":"..."},"risk":"low"}]}
"""


class Planner:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def _extract_json(self, text: str) -> Optional[dict]:
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def _validate_plan(self, plan: dict, allowed_tools: set) -> tuple[bool, str]:
        if not isinstance(plan, dict):
            return False, "Plan is not an object"
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            return False, "Plan has no steps"
        if len(steps) > 8:
            return False, "Too many steps (max 8)"
        for i, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                return False, f"Step {i} invalid"
            tool = step.get("tool")
            if tool not in allowed_tools:
                return False, f"Step {i} uses unknown tool: {tool}"
            if not isinstance(step.get("arguments"), dict):
                return False, f"Step {i} arguments must be an object"
            args = step.get("arguments") or {}
            if tool == "write_developer_code":
                if not args.get("filepath"):
                    return False, f"Step {i} write_developer_code missing filepath"
                if "content" not in args or args.get("content") is None:
                    return False, f"Step {i} write_developer_code missing content"
            if tool == "open_app" and not args.get("target"):
                return False, f"Step {i} open_app missing target"
            check = step.get("success_check") or {"type": "always_true"}
            if not isinstance(check, dict) or "type" not in check:
                return False, f"Step {i} missing success_check.type"
            step.setdefault("id", i)
            step.setdefault("title", f"Step {i}")
            step.setdefault("risk", "low")
            step["success_check"] = check
        plan.setdefault("goal", "")
        plan.setdefault("needs_confirmation", False)
        return True, "ok"

    def _desktop_path(self, *parts: str) -> str:
        home = os.path.expanduser("~").replace("\\", "/")
        return "/".join([home, "Desktop", *[p for p in parts if p]])

    def _normalize_filename(self, name: str) -> str:
        name = (name or "").strip().strip("\"'")
        name = name.replace("\\", "/").split("/")[-1]
        if not name:
            return "note.txt"
        if "." not in name:
            name = name + ".txt"
        return name

    def _extract_file_content(self, t: str) -> str:
        """Pull file body text from common phrasings."""
        # write CONTENT to file.ext  (content must not swallow the filename)
        m = re.search(
            r"\bwrite\s+[\"']?(.+?)[\"']?\s+to\s+[\"']?[A-Za-z0-9_\-]+\.(?:txt|md|py|json|log|csv)",
            t,
            re.IGNORECASE,
        )
        if m:
            content = m.group(1).strip().strip("\"'")
            if content:
                return content

        patterns = [
            r"(?:with\s+text|with\s+content|containing|that\s+says|saying|inside(?:\s+it)?)\s+[\"'](.+?)[\"']\s*$",
            r"(?:with\s+text|with\s+content|containing|that\s+says|saying|inside(?:\s+it)?)\s+[\"']?(.+?)[\"']?\s*$",
            r"(?:write|content)\s+[\"'](.+?)[\"']\s*$",
            r"\btext\s+[\"'](.+?)[\"']\s*$",
            r"\btext\s+([A-Za-z0-9_\-.,:;!?'\" ]+?)\s*$",
        ]
        for pat in patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                content = m.group(1).strip().strip("\"'")
                content = re.sub(r"\s+inside\s+it\s*$", "", content, flags=re.I).strip()
                content = re.sub(r"\s+on\s+(the\s+)?desktop\s*$", "", content, flags=re.I).strip()
                # Don't treat a bare filename as content
                if re.fullmatch(r"[A-Za-z0-9_\-]+\.[A-Za-z0-9]+", content):
                    continue
                if content:
                    return content
        return "OK"

    def _extract_filename(self, t: str) -> Optional[str]:
        """Find a filename from many create/write phrasings."""
        patterns = [
            # file named / called X
            r"(?:file|text\s+file)\s+(?:named|called)\s+[\"']?([A-Za-z0-9_\-\.]+)[\"']?",
            # create/make/write [a] file X
            r"(?:create|make|write)\s+(?:a\s+)?(?:text\s+)?file\s+[\"']?([A-Za-z0-9_\-\.]+)[\"']?",
            # create X.txt / make notes.md (extension required to avoid grabbing random words)
            r"(?:create|make|write)\s+[\"']?([A-Za-z0-9_\-]+\.(?:txt|md|py|json|log|csv|ini|cfg|yaml|yml|html|css|js))[\"']?",
            # named X on desktop
            r"named\s+[\"']?([A-Za-z0-9_\-\.]+)[\"']?",
            # to desktop/file or Desktop\file
            r"(?:desktop|Desktop)[/\\]([A-Za-z0-9_\-\.]+)",
            # write ... to FILENAME
            r"\bto\s+[\"']?([A-Za-z0-9_\-\.]+\.(?:txt|md|py|json|log|csv))[\"']?",
        ]
        for pat in patterns:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                return self._normalize_filename(m.group(1))
        return None

    def _try_fast_file_plan(self, user_goal: str) -> Optional[Dict[str, Any]]:
        """
        Common create-file cases without LLM.
        Covers folder+file and simple desktop file with many phrasings.
        """
        t = user_goal.strip()
        low = t.lower()

        # Must look like a create/write file goal (avoid false positives on chat)
        create_cues = (
            "create", "make", "write", "save", "put", "generate",
            "file", "desktop", ".txt", ".md", ".py", ".json", ".log", ".csv",
        )
        if not any(c in low for c in create_cues):
            return None

        m_folder = re.search(
            r"(?:folder|directory)\s+(?:named\s+|called\s+)?[\"']?([A-Za-z0-9_\-]+)[\"']?",
            t,
            re.IGNORECASE,
        )
        filename = self._extract_filename(t)
        if not filename:
            return None

        # Prefer desktop unless user clearly said otherwise (Documents etc. → still desktop for fast path safety)
        folder = m_folder.group(1) if m_folder else None
        content = self._extract_file_content(t)

        path = self._desktop_path(folder, filename) if folder else self._desktop_path(filename)
        title = f"Create {folder}/{filename}" if folder else f"Create {filename} on desktop"
        return {
            "goal": user_goal,
            "needs_confirmation": True,
            "steps": [
                {
                    "id": 1,
                    "title": title,
                    "tool": "write_developer_code",
                    "arguments": {"filepath": path, "content": content},
                    "success_check": {
                        "type": "file_contains",
                        "path": path,
                        "text": content,
                    },
                    "risk": "low",
                }
            ],
        }

    def _try_fast_open_and_file_plan(self, user_goal: str) -> Optional[Dict[str, Any]]:
        """Open app + create file in one goal without LLM."""
        t = user_goal.strip()
        m_open = re.search(
            r"open\s+(notepad|calculator|calc|chrome|edge|explorer|cmd|powershell)",
            t,
            re.IGNORECASE,
        )
        if not m_open:
            return None
        filename = self._extract_filename(t)
        if not filename:
            return None
        app = m_open.group(1).lower()
        target = {"calculator": "calc.exe", "calc": "calc.exe"}.get(app, app)
        content = self._extract_file_content(t)
        path = self._desktop_path(filename)
        return {
            "goal": user_goal,
            "needs_confirmation": True,
            "steps": [
                {
                    "id": 1,
                    "title": f"Open {target}",
                    "tool": "open_app",
                    "arguments": {"target": target},
                    "success_check": {"type": "always_true"},
                    "risk": "low",
                },
                {
                    "id": 2,
                    "title": f"Create {filename}",
                    "tool": "write_developer_code",
                    "arguments": {"filepath": path, "content": content},
                    "success_check": {
                        "type": "file_contains",
                        "path": path,
                        "text": content,
                    },
                    "risk": "low",
                },
            ],
        }

    def create_plan(
        self,
        user_goal: str,
        tool_schemas: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        allowed = set()
        compact_lines = []
        for schema in tool_schemas:
            fn = schema.get("function") or schema
            name = fn.get("name", "")
            if not name:
                continue
            allowed.add(name)
            desc = (fn.get("description") or "")[:120]
            props = (fn.get("parameters") or {}).get("properties") or {}
            keys = ", ".join(props.keys())
            compact_lines.append(f"- {name}({keys}): {desc}")

        # Fast paths: create-file / open+file without LLM (seconds, not 60–90s)
        # Prefer open+file when both appear so notepad opens too
        if "open_app" in allowed and "write_developer_code" in allowed:
            fast2 = self._try_fast_open_and_file_plan(user_goal)
            if fast2:
                ok, reason = self._validate_plan(fast2, allowed)
                if ok:
                    return {"ok": True, "plan": fast2, "raw": "fast_path_open_file"}

        if "write_developer_code" in allowed:
            fast = self._try_fast_file_plan(user_goal)
            if fast:
                ok, reason = self._validate_plan(fast, allowed)
                if ok:
                    return {"ok": True, "plan": fast, "raw": "fast_path_file"}

        tools_block = "\n".join(compact_lines) if compact_lines else "(no tools)"
        messages = [
            {"role": "system", "content": PLAN_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Tools:\n{tools_block}\n\n"
                    f"Goal:\n{user_goal}\n\n"
                    "JSON plan only."
                ),
            },
        ]

        try:
            # tools key omit — timeout 90s
            response = self.llm.chat(messages=messages, tools=None, timeout=90)
            content = (response.choices[0].message.content or "").strip()
        except Exception as e:
            return {"ok": False, "error": f"LLM planner failed: {e}", "raw": ""}

        plan = self._extract_json(content)
        if not plan:
            return {
                "ok": False,
                "error": "Planner did not return valid JSON",
                "raw": content[:500],
            }

        valid, reason = self._validate_plan(plan, allowed)
        if not valid:
            return {
                "ok": False,
                "error": f"Invalid plan: {reason}",
                "raw": content[:500],
                "plan": plan,
            }

        return {"ok": True, "plan": plan, "raw": content}

    @staticmethod
    def should_use_planner(user_text: str) -> bool:
        t = user_text.lower().strip()
        if not t:
            return False
        # Multi-step / explicit plan cues
        # Avoid bare " and " / " also " — they match pure analysis sentences
        cues = [
            " then ",
            " after that",
            "create a folder",
            "create folder",
            "make a folder",
            "then open",
            "then write",
            "then play",
            "step by step",
            "first create",
            "first write",
            "first open",
            "and then ",
            "and also create",
            "and also write",
            "and open ",
            "and write ",
            "and create ",
        ]
        if any(c in t for c in cues):
            return True
        # Analysis / summarize — never multi-step tool plans
        analysis = [
            "summarize", "summarise", "list gaps", "how each",
            "supports ", "support ", "explain how", "map each",
            "based on the", "from the previous", "from the cv",
        ]
        if any(c in t for c in analysis) and not any(
            x in t for x in ("create file", "write file", "create folder", "open app")
        ):
            return False
        # Simple create/write file on desktop — route to planner so fast-path can run
        file_cues = [
            "create a file",
            "create file",
            "make a file",
            "make file",
            "write a file",
            "write file",
            "create a text file",
            "make a text file",
            "on desktop",
            "on the desktop",
            "to desktop",
        ]
        if any(c in t for c in file_cues) and any(
            x in t for x in ("file", ".txt", ".md", ".py", ".json", ".log", ".csv", "desktop")
        ):
            return True
        # Filename with extension + create/write/make/save
        if re.search(r"\b[a-z0-9_\-]+\.(txt|md|py|json|log|csv)\b", t) and re.search(
            r"\b(create|make|write|save|put)\b", t
        ):
            return True
        verbs = [
            "create",
            "write",
            "open",
            "play",
            "install",
            "delete",
            "copy",
            "move",
            "run",
            "start",
            "stop",
        ]
        hit = sum(1 for v in verbs if re.search(rf"\b{v}\b", t))
        return hit >= 2
