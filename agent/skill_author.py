# agent/skill_author.py
"""
Phase 3 — Author custom skills from plain text / markdown / .txt|.md import.

Generates a safe plugin (allowed tools only), registers a skill pack, and
stores trigger→outcome plans under history/custom_skills/.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.storage import atomic_write_json, load_json
from agent.data_protection import DataProtectionError

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = Path(".").resolve()

CUSTOM_DIR = Path(BASE_DIR) / "history" / "custom_skills"
PLUGINS_DIR = Path(BASE_DIR) / "plugins"

# Only these tools may appear in generated skills (security).
ALLOWED_TOOLS = {
    "open_app",
    "browser_open",
    "web_search_open",
    "office_open",
    "office_new",
    "vscode_open",
    "write_developer_code",
    "youtube_player",
    "list_installed_apps",
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return (s[:40] or "custom_skill")


def parse_skill_markdown(text: str) -> Dict[str, Any]:
    """
    Preferred markdown shape:

    # skill_id: my_skill
    title: My Skill
    description: ...
    triggers:
    - phrase one
    - phrase two
    actions:
    - tool: browser_open
      url: https://example.com
    - tool: open_app
      target: notepad
    samples:
    - phrase one
    """
    raw = (text or "").strip()
    data: Dict[str, Any] = {
        "skill_id": "",
        "title": "",
        "description": "",
        "triggers": [],
        "actions": [],
        "samples": [],
    }
    if not raw:
        return data

    # skill_id from header
    m = re.search(r"#\s*skill_id\s*:\s*([a-zA-Z0-9_\-]+)", raw, re.I)
    if m:
        data["skill_id"] = _slug(m.group(1))

    def _section(name: str) -> str:
        pat = rf"(?im)^[ \t]*{name}[ \t]*:\s*$"
        m1 = re.search(pat, raw)
        if not m1:
            # inline title: value
            m2 = re.search(rf"(?im)^[ \t]*{name}[ \t]*:\s*(.+)$", raw)
            return (m2.group(1).strip() if m2 else "")
        start = m1.end()
        m3 = re.search(r"(?im)^[ \t]*[a-z_][a-z0-9_ \t]*:\s*$", raw[start:])
        end = start + (m3.start() if m3 else len(raw[start:]))
        return raw[start:end].strip()

    title = _section("title")
    if title and "\n" not in title:
        data["title"] = title.strip()
    desc = _section("description")
    if desc and not desc.lstrip().startswith("-"):
        data["description"] = desc.split("\n")[0].strip()
    else:
        # multi-line description block
        m = re.search(r"(?im)^description\s*:\s*(.+?)(?=^[ \t]*[a-z_]+\s*:|\Z)", raw, re.S)
        if m:
            data["description"] = re.sub(r"\s+", " ", m.group(1)).strip()

    def _bullets(section_name: str) -> List[str]:
        body = _section(section_name)
        items = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("-"):
                items.append(line[1:].strip())
            elif line:
                items.append(line)
        return [x for x in items if x]

    data["triggers"] = _bullets("triggers")
    data["samples"] = _bullets("samples") or list(data["triggers"][:3])

    # actions: YAML-ish list of tool blocks
    actions_body = _section("actions")
    current: Dict[str, Any] = {}
    for line in actions_body.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        tm = re.match(r"^\s*-\s*tool\s*:\s*(\w+)\s*$", line, re.I)
        if tm:
            if current.get("tool"):
                data["actions"].append(current)
            current = {"tool": tm.group(1).strip()}
            continue
        km = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)\s*$", line)
        if km and current is not None:
            key = km.group(1).strip()
            val = km.group(2).strip().strip('"').strip("'")
            if key != "tool":
                current[key] = val
    if current.get("tool"):
        data["actions"].append(current)

    if not data["skill_id"]:
        data["skill_id"] = _slug(data["title"] or data["triggers"][0] if data["triggers"] else "custom_skill")
    if not data["title"]:
        data["title"] = data["skill_id"].replace("_", " ").title()
    if not data["description"]:
        data["description"] = f"Custom skill: {data['title']}"
    return data


def plain_text_to_skill(text: str) -> Dict[str, Any]:
    """Heuristic: turn free text into a reviewable skill dict."""
    raw = (text or "").strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    title = lines[0][:60] if lines else "Custom Skill"
    skill_id = _slug(title)

    triggers: List[str] = []
    actions: List[Dict[str, Any]] = []
    desc_parts: List[str] = []

    for ln in lines:
        low = ln.lower()
        # URLs
        for url in re.findall(r"https?://\S+", ln):
            actions.append({"tool": "browser_open", "url": url.rstrip(".,)")})
        # search for X
        m = re.search(r"search\s+(?:the\s+web\s+for\s+|google\s+for\s+)?(.+)$", ln, re.I)
        if m and "http" not in low:
            actions.append({"tool": "web_search_open", "query": m.group(1).strip(), "engine": "google"})
        # open app
        m = re.search(r"\bopen\s+([a-zA-Z0-9_.+\- ]{2,40})$", ln, re.I)
        if m and "http" not in low and "vscode" not in low:
            target = m.group(1).strip()
            if target.lower() in ("word", "excel", "powerpoint", "outlook", "onenote"):
                actions.append({"tool": "office_open", "app": target.lower()})
            else:
                actions.append({"tool": "open_app", "target": target})
        # create csv/file
        m = re.search(r"create\s+(?:a\s+)?csv\s+(?:named\s+)?([a-zA-Z0-9_.\-]+)", ln, re.I)
        if m:
            actions.append({"tool": "office_new", "kind": "csv", "name": m.group(1), "open_after": "true"})
        # vscode
        m = re.search(r"open\s+(?:in\s+)?(?:vs\s*code|vscode)\s+(.+)$", ln, re.I)
        if m:
            actions.append({"tool": "vscode_open", "path": m.group(1).strip(), "editor": "code"})
        # trigger lines
        if low.startswith("trigger:") or low.startswith("say:"):
            triggers.append(re.sub(r"^(trigger|say)\s*:\s*", "", ln, flags=re.I).strip())
        elif len(ln) < 50 and not re.search(r"https?://", ln) and not low.startswith("open "):
            if ln != lines[0]:
                desc_parts.append(ln)

    if not triggers:
        triggers = [title.lower(), f"run {title.lower()}"]
    if not actions:
        # safe default: search web for the title
        actions.append({"tool": "web_search_open", "query": title, "engine": "google"})

    # dedupe actions
    seen = set()
    uniq = []
    for a in actions:
        key = json.dumps(a, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(a)

    return {
        "skill_id": skill_id,
        "title": title[:80],
        "description": (" ".join(desc_parts)[:200] if desc_parts else f"Custom skill generated from text: {title}"),
        "triggers": triggers[:8],
        "actions": uniq[:10],
        "samples": triggers[:3],
    }


def skill_to_markdown(skill: Dict[str, Any]) -> str:
    lines = [
        f"# skill_id: {skill.get('skill_id')}",
        f"title: {skill.get('title')}",
        f"description: {skill.get('description')}",
        "triggers:",
    ]
    for t in skill.get("triggers") or []:
        lines.append(f"- {t}")
    lines.append("actions:")
    for a in skill.get("actions") or []:
        tool = a.get("tool", "")
        lines.append(f"- tool: {tool}")
        for k, v in a.items():
            if k == "tool":
                continue
            lines.append(f"  {k}: {v}")
    lines.append("samples:")
    for s in skill.get("samples") or skill.get("triggers") or []:
        lines.append(f"- {s}")
    return "\n".join(lines) + "\n"


def validate_skill(skill: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    sid = (skill.get("skill_id") or "").strip()
    if not sid or not re.match(r"^[a-z][a-z0-9_]{1,39}$", sid):
        errors.append("skill_id must be snake_case starting with a letter (e.g. morning_brief)")
    if not (skill.get("title") or "").strip():
        errors.append("title is required")
    triggers = skill.get("triggers") or []
    if not triggers:
        errors.append("at least one trigger phrase is required")
    actions = skill.get("actions") or []
    if not actions:
        errors.append("at least one action is required")
    for i, a in enumerate(actions):
        tool = (a.get("tool") or "").strip()
        if tool not in ALLOWED_TOOLS:
            errors.append(f"action {i+1}: tool '{tool}' not allowed. Allowed: {', '.join(sorted(ALLOWED_TOOLS))}")
        if tool == "browser_open" and not a.get("url"):
            errors.append(f"action {i+1}: browser_open needs url")
        if tool == "web_search_open" and not a.get("query"):
            errors.append(f"action {i+1}: web_search_open needs query")
        if tool == "open_app" and not a.get("target"):
            errors.append(f"action {i+1}: open_app needs target")
        if tool == "write_developer_code" and not a.get("filepath"):
            errors.append(f"action {i+1}: write_developer_code needs filepath")
    return (len(errors) == 0, errors)


def _actions_to_steps(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    steps = []
    for i, a in enumerate(actions, start=1):
        tool = a.get("tool")
        args = {k: v for k, v in a.items() if k != "tool"}
        # coerce booleans
        for k, v in list(args.items()):
            if isinstance(v, str) and v.lower() in ("true", "false"):
                args[k] = v.lower() == "true"
        steps.append(
            {
                "id": i,
                "title": f"{tool}",
                "tool": tool,
                "arguments": args,
                "risk": "low",
                "success_check": {"type": "always_true"},
            }
        )
    return steps


def generate_plugin_source(skill: Dict[str, Any]) -> str:
    """Generate a thin plugin that reads its encrypted skill manifest at runtime."""
    sid = skill["skill_id"]
    class_name = "".join(p.title() for p in sid.split("_")) + "SkillTool"
    tool_name = f"run_{sid}"
    return f'''# plugins/{sid}_skill.py
"""Auto-generated custom skill loader. Skill content remains in encrypted local storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from tools.base import BaseTool
from config import BASE_DIR
from agent.storage import load_json


class {class_name}(BaseTool):
    @property
    def name(self) -> str:
        return "{tool_name}"

    @property
    def description(self) -> str:
        return "Loads the saved custom skill plan from protected local storage."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {{
            "type": "object",
            "properties": {{}},
            "required": [],
        }}

    def execute(self, **kwargs) -> Dict[str, Any]:
        manifest_path = Path(BASE_DIR) / "history" / "custom_skills" / "{sid}.json"
        manifest = load_json(manifest_path, encrypt=True)
        if not isinstance(manifest, dict):
            return {{"content": "Custom skill manifest is unavailable or invalid.", "is_error": True}}
        actions = manifest.get("actions") or []
        lines = [f"Skill '{{manifest.get('title') or '{sid}'}}' actions:"]
        for action in actions:
            if not isinstance(action, dict):
                continue
            lines.append(f"  - {{action.get('tool')}}: {{ {{k: v for k, v in action.items() if k != 'tool'}} }}")
        return {{"content": "\\n".join(lines), "is_error": False}}
'''


def install_skill(skill: Dict[str, Any], base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Write plugin, manifest, update custom packs index.
    Returns paths + how_to samples.
    """
    ok, errors = validate_skill(skill)
    if not ok:
        return {"ok": False, "errors": errors}

    root = Path(base_dir) if base_dir else Path(BASE_DIR)
    custom = root / "history" / "custom_skills"
    plugins = root / "plugins"
    custom.mkdir(parents=True, exist_ok=True)
    plugins.mkdir(parents=True, exist_ok=True)

    sid = skill["skill_id"]
    tool_name = f"run_{sid}"
    # Ensure pack tools list includes generated tool + underlying tools
    tools = [tool_name] + [a.get("tool") for a in skill.get("actions") or [] if a.get("tool")]
    tools = list(dict.fromkeys(tools))

    manifest = {
        "skill_id": sid,
        "title": skill.get("title"),
        "description": skill.get("description"),
        "triggers": skill.get("triggers") or [],
        "samples": skill.get("samples") or skill.get("triggers") or [],
        "actions": skill.get("actions") or [],
        "tools": tools,
        "enabled": True,
        "steps": _actions_to_steps(skill.get("actions") or []),
    }

    manifest_path = custom / f"{sid}.json"
    atomic_write_json(manifest_path, manifest, indent=2, ensure_ascii=False, encrypt=True)

    plugin_path = plugins / f"{sid}_skill.py"
    plugin_path.write_text(generate_plugin_source(skill), encoding="utf-8")

    # Index
    index_path = custom / "index.json"
    index: Dict[str, Any] = {"skills": []}
    if index_path.exists():
        index = load_json(index_path, default={"skills": []}, encrypt=True)
        if not isinstance(index, dict):
            raise ValueError("custom skill index must be a JSON object")
    skills = [s for s in index.get("skills", []) if s.get("skill_id") != sid]
    skills.append({"skill_id": sid, "title": skill.get("title"), "manifest": manifest_path.name})
    index["skills"] = skills
    atomic_write_json(index_path, index, indent=2, encrypt=True)

    samples = manifest.get("samples") or []
    how_to = (
        f"Skill '{manifest['title']}' installed.\n"
        f"Plugin: plugins/{sid}_skill.py\n"
        f"Manifest: history/custom_skills/{sid}.json\n"
        f"Enable: enable skill {sid}\n"
        f"Try saying:\n"
        + "\n".join(f"  • {s}" for s in samples[:5])
    )
    return {
        "ok": True,
        "skill_id": sid,
        "plugin_path": str(plugin_path),
        "manifest_path": str(manifest_path),
        "how_to": how_to,
        "samples": samples,
        "tool_name": tool_name,
    }


def load_all_custom_manifests(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(base_dir) if base_dir else Path(BASE_DIR)
    custom = root / "history" / "custom_skills"
    if not custom.is_dir():
        return []
    out = []
    for path in sorted(custom.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            data = load_json(path, encrypt=True)
            if isinstance(data, dict) and data.get("skill_id"):
                out.append(data)
        except DataProtectionError:
            raise
        except Exception:
            continue
    return out
