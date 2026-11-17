# agent/policy.py
"""
Phase 3 — Policy engine (local-first).

Controls risk defaults, tool allow/deny, trusted scheduled opens,
and safe write roots. Team roles can layer on later.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.storage import atomic_write_json, load_json
from agent.data_protection import DataProtectionError

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = Path(".").resolve()

POLICY_FILE = Path(BASE_DIR) / "history" / "agent_policy.json"

MUTATING_TOOL_NAME_PARTS = {
    "add", "clear", "close", "commit", "configure", "control", "copy",
    "create", "delete", "deploy", "download", "edit", "execute", "format",
    "install", "kill", "manage", "merge", "move", "new", "open", "publish",
    "push", "remove", "rename", "restart", "schedule", "send", "set",
    "start", "stop", "submit", "terminate", "update", "upload", "write",
}


def _tool_name_looks_mutating(tool_name: str) -> bool:
    parts = {part for part in str(tool_name or "").lower().replace("-", "_").split("_") if part}
    return bool(parts & MUTATING_TOOL_NAME_PARTS)


DEFAULT_POLICY: Dict[str, Any] = {
    "version": 1,
    "role": "operator",  # future: admin | operator | read_only
    "require_gate_for": {
        "write_developer_code": True,
        "execute_powershell_admin": True,
        "install_python_dependencies": True,
        "screen_controller": True,
    },
    "auto_approve_scheduled_opens": [
        "notepad",
        "notepad.exe",
        "calculator",
        "calc",
        "calc.exe",
        "explorer",
        "explorer.exe",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "chrome",
        "chrome.exe",
        "edge",
        "msedge",
        "msedge.exe",
    ],
    "denied_tools": [],
    "allowed_write_roots": [
        "Desktop",
        "Documents",
        "Downloads",
    ],
    "deny_write_extensions": [".exe", ".bat", ".cmd", ".ps1", ".msi", ".scr"],
    "max_risk_without_confirm": "low",  # low | medium | high
}


class PolicyEngine:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else POLICY_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, Any] = json.loads(json.dumps(DEFAULT_POLICY))
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        raw = load_json(self.path, default=None, encrypt=True)
        if raw is not None:
            if not isinstance(raw, dict):
                raise ValueError("policy store must be a JSON object")
            # merge so new keys appear after upgrades
            merged = json.loads(json.dumps(DEFAULT_POLICY))
            merged.update(raw)
            self.data = merged

    def save(self) -> None:
        atomic_write_json(self.path, self.data, indent=2, ensure_ascii=False, encrypt=True)

    @property
    def role(self) -> str:
        return str(self.data.get("role") or "operator")

    def is_tool_denied(self, tool_name: str) -> bool:
        denied = self.data.get("denied_tools") or []
        return tool_name in denied

    def requires_gate(self, tool_name: str) -> bool:
        if self.role == "read_only":
            return True
        req = self.data.get("require_gate_for") or {}
        if tool_name in req:
            return bool(req[tool_name])
        # default: mutating-ish names need gate
        return _tool_name_looks_mutating(tool_name) or any(
            marker in tool_name.lower() for marker in ("screen", "powershell")
        )

    def trusted_scheduled_opens(self) -> Set[str]:
        items = self.data.get("auto_approve_scheduled_opens") or []
        return {str(x).strip().lower() for x in items if x}

    def is_trusted_scheduled_open(self, target: str) -> bool:
        t = (target or "").strip().lower().replace("\\", "/")
        base = t.split("/")[-1]
        trusted = self.trusted_scheduled_opens()
        return base in trusted or t in trusted

    def evaluate_write_path(self, filepath: str) -> Tuple[bool, str]:
        """Validate a write destination against canonical allowed roots.

        Resolving both paths before comparing them prevents string-segment
        lookalikes and existing symlink/junction escapes from passing policy.
        """
        if self.role == "read_only":
            return False, "Role read_only cannot write files"

        raw_path = (filepath or "").strip().strip('"').strip("'")
        if not raw_path:
            return False, "Empty filepath"

        ext = Path(raw_path).suffix.lower()
        deny_ext = {str(x).lower() for x in (self.data.get("deny_write_extensions") or [])}
        if ext in deny_ext:
            return False, f"Policy denies writing extension {ext}"

        configured_roots = [str(x).strip() for x in (self.data.get("allowed_write_roots") or []) if str(x).strip()]
        if not configured_roots:
            return False, "No allowed_write_roots are configured; refusing file write by default"

        try:
            # FileWriterTool supports Desktop-relative paths; normalize the same
            # shorthand here before applying the security boundary.
            home = str(Path.home()).replace("\\", "/")
            for placeholder in ("YourUsername", "YourName", "yourusername", "username", "YourUserName"):
                if placeholder in raw_path:
                    raw_path = raw_path.replace(f"C:/Users/{placeholder}", home).replace(
                        placeholder, Path(home).name
                    )
            portable_path = raw_path.replace("\\", "/")
            if portable_path.lower() == "desktop":
                raw_path = str(Path.home() / "Desktop")
            elif portable_path.lower().startswith("desktop/"):
                raw_path = str(Path.home() / "Desktop" / portable_path.split("/", 1)[1])
            candidate = Path(os.path.expandvars(os.path.expanduser(raw_path))).resolve(strict=False)
            allowed_roots = []
            for root in configured_roots:
                expanded = Path(os.path.expandvars(os.path.expanduser(root)))
                if not expanded.is_absolute():
                    expanded = Path.home() / expanded
                allowed_roots.append(expanded.resolve(strict=False))

            candidate_norm = os.path.normcase(str(candidate))
            for allowed in allowed_roots:
                allowed_norm = os.path.normcase(str(allowed))
                try:
                    if os.path.commonpath([candidate_norm, allowed_norm]) == allowed_norm:
                        return True, "ok"
                except ValueError:
                    # Different drives on Windows cannot share a common path.
                    continue
        except (OSError, RuntimeError, ValueError) as e:
            return False, f"Could not safely canonicalize write path: {e}"

        return False, (
            "Path outside canonical allowed_write_roots "
            f"({', '.join(configured_roots)}). Refusing write to: {raw_path}"
        )

    def evaluate_tool_call(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        permissions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Pre-gate policy check.
        Returns: allowed, require_gate, risk_hint, reason
        """
        arguments = arguments or {}
        if self.is_tool_denied(tool_name):
            return {
                "allowed": False,
                "require_gate": True,
                "risk_hint": "high",
                "reason": f"Tool '{tool_name}' is denied by policy",
            }

        declared_permissions = {str(p).strip().lower() for p in (permissions or [])}
        mutating_permissions = {
            "filesystem:write", "process:execute", "system:admin",
            "network:write", "clipboard:write", "registry:write",
            "filesystem:delete", "process:terminate", "screen:control",
            "system:environment:write", "email:send", "messaging:send",
            "docker:control", "service:control",
        }
        if self.role == "read_only" and (
            tool_name in ("write_developer_code", "execute_powershell_admin", "install_python_dependencies")
            or bool(declared_permissions & mutating_permissions)
            or _tool_name_looks_mutating(tool_name)
        ):
            return {
                "allowed": False,
                "require_gate": True,
                "risk_hint": "high",
                "reason": "Role read_only blocks mutating tools",
            }

        reason = "ok"
        risk_hint = "low"
        if tool_name == "write_developer_code":
            ok, reason = self.evaluate_write_path(str(arguments.get("filepath") or ""))
            if not ok:
                return {
                    "allowed": False,
                    "require_gate": True,
                    "risk_hint": "high",
                    "reason": reason,
                }
            risk_hint = "low"

        if tool_name == "execute_powershell_admin":
            risk_hint = "medium"

        return {
            "allowed": True,
            "require_gate": self.requires_gate(tool_name),
            "risk_hint": risk_hint,
            "reason": reason,
        }
