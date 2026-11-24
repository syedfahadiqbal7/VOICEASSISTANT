"""Static, non-executing inventory of plugin capability usage.

This is a migration aid, not a security scanner or proof that a plugin only uses
its declared permissions. Python reflection, aliases, dynamic imports and native
extensions can hide behavior; treat every result as a conservative signal.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


CAPABILITY_RULES = {
    "filesystem:read": {
        "calls": {"open", "read_text", "read_bytes", "iterdir", "glob", "rglob", "listdir", "scandir", "walk"},
    },
    "filesystem:write": {
        "calls": {"write_text", "write_bytes", "mkdir", "makedirs", "copy", "copy2", "move", "replace"},
    },
    "filesystem:delete": {
        "calls": {"unlink", "remove", "rmdir", "removedirs", "rmtree"},
    },
    "process:execute": {
        "calls": {"Popen", "call", "check_call", "check_output", "system", "startfile", "CreateProcess"},
    },
    "process:read": {
        "calls": {"process_iter", "Process", "pids", "pid_exists"},
    },
    "process:terminate": {
        "calls": {"terminate", "kill", "send_signal"},
    },
    "network:read": {
        "calls": {"get", "head", "urlopen", "urlretrieve", "recv"},
    },
    "network:write": {
        "calls": {"post", "put", "patch", "delete", "sendall"},
    },
    "clipboard:read": {"calls": {"GetClipboardData", "OpenClipboard"}},
    "clipboard:write": {"calls": {"clipboard_clear", "SetClipboardData", "EmptyClipboard"}},
    "registry:read": {"calls": {"OpenKey", "QueryValue", "QueryValueEx", "EnumKey", "EnumValue"}},
    "registry:write": {"calls": {"CreateKey", "SetValue", "SetValueEx", "DeleteKey", "DeleteValue"}},
    "screen:read": {"calls": {"screenshot", "grab", "pixel", "locateOnScreen"}},
    "screen:control": {"calls": {"doubleClick", "moveTo", "hotkey"}},
    "email:read": {"calls": {"uid"}},
    "email:send": {"calls": {"sendmail", "send_message", "send_message_async"}},
    "messaging:send": {"calls": {"send_message", "send_text", "send_chat_message"}},
    "service:control": {"calls": {"StartService", "StopService", "ChangeServiceConfig", "OpenSCManager"}},
    "docker:control": {"calls": {"containers", "images", "exec_run"}},
    "system:environment:write": {"calls": {"putenv", "unsetenv"}},
}

IMPORT_CAPABILITIES = {
    "subprocess": {"process:execute"},
    "psutil": {"process:read"},
    "requests": {"network:read"},
    "httpx": {"network:read"},
    "aiohttp": {"network:read"},
    "urllib": {"network:read"},
    "socket": {"network:read"},
    "pyperclip": {"clipboard:read", "clipboard:write"},
    "win32clipboard": {"clipboard:read", "clipboard:write"},
    "winreg": {"registry:read", "registry:write"},
    "pyautogui": {"screen:read", "screen:control"},
    "mss": {"screen:read"},
    "docker": {"docker:control"},
    "smtplib": {"email:send"},
    "imaplib": {"email:read"},
    "win32service": {"service:control"},
    "win32serviceutil": {"service:control"},
}

NATIVE_IMPORTS = {
    "ctypes", "win32api", "win32con", "win32process", "win32security",
    "pythoncom", "win32com", "comtypes", "clr",
}


def _dotted_name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def inspect_source(source_path: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inspect Python syntax without importing or executing plugin code."""
    source_path = Path(source_path)
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    tree = ast.parse(raw.decode("utf-8-sig"), filename=str(source_path))

    imports: set[str] = set()
    observed: set[str] = set()
    evidence: dict[str, set[str]] = {}
    warnings: set[str] = set()

    def record(capability: str, reason: str) -> None:
        observed.add(capability)
        evidence.setdefault(capability, set()).add(reason)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                imports.add(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])

    for module in sorted(imports):
        for capability in IMPORT_CAPABILITIES.get(module, set()):
            record(capability, "import:" + module)
        if module in NATIVE_IMPORTS:
            warnings.add("native_or_COM_API:" + module)
        if module in {"importlib", "__import__"}:
            warnings.add("dynamic_import")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            leaf = name.rsplit(".", 1)[-1]
            for capability, rule in CAPABILITY_RULES.items():
                if leaf in rule["calls"]:
                    record(capability, "call:" + name)
            if leaf in {"eval", "exec", "compile"}:
                warnings.add("dynamic_code_execution:" + leaf)
            if leaf in {"connect", "urlopen", "request", "get", "post", "put", "delete"}:
                if any(part in name.lower() for part in ("requests", "httpx", "urllib", "socket", "aiohttp")):
                    record("network:read" if leaf not in {"post", "put", "delete"} else "network:write", "call:" + name)
            if leaf in {"read_text", "read_bytes", "open", "listdir", "scandir", "walk"}:
                record("filesystem:read", "call:" + name)
            if leaf in {"write_text", "write_bytes", "makedirs", "mkdir"}:
                record("filesystem:write", "call:" + name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _dotted_name(target.value).endswith("environ"):
                    record("system:environment:write", "assignment:os.environ")
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _dotted_name(target.value).endswith("environ"):
                    record("system:environment:write", "delete:os.environ")

    declared = sorted({str(x).strip().lower() for x in (manifest or {}).get("permissions", []) if str(x).strip()})
    observed_list = sorted(observed)
    return {
        "file": source_path.name,
        "sha256": digest,
        "declared_permissions": declared,
        "observed_capabilities": observed_list,
        "undeclared_observed": sorted(observed - set(declared)),
        "possibly_unused_declared": sorted(set(declared) - observed),
        "imports": sorted(imports),
        "warnings": sorted(warnings),
        "evidence": {key: sorted(value) for key, value in sorted(evidence.items())},
        "analysis_limit": "Static AST heuristics only; aliases, reflection, dynamic imports, native code and indirect calls may be missed.",
    }


def build_inventory(plugins_dir: Path) -> dict[str, Any]:
    root = Path(plugins_dir)
    rows = []
    for source in sorted(root.glob("*.py")):
        if source.name.startswith("_"):
            continue
        manifest_path = source.with_suffix(".manifest.json")
        manifest: dict[str, Any] = {}
        manifest_error = None
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("manifest must be a JSON object")
            except Exception as error:
                manifest_error = type(error).__name__
                manifest = {}
        else:
            manifest_error = "missing_manifest"
        try:
            row = inspect_source(source, manifest)
            row["plugin_name"] = str(manifest.get("name") or source.stem)
            row["manifest_error"] = manifest_error
            expected = str(manifest.get("checksum_sha256") or "").lower()
            row["checksum_matches"] = bool(expected and expected == row["sha256"])
            row["declared_tools"] = manifest.get("tools") if isinstance(manifest.get("tools"), list) else []
            rows.append(row)
        except (OSError, UnicodeError, SyntaxError, ValueError) as error:
            rows.append({
                "file": source.name,
                "plugin_name": source.stem,
                "scan_error": type(error).__name__,
                "manifest_error": manifest_error,
            })

    return {
        "schema_version": 1,
        "plugins_dir": str(root),
        "plugin_count": len(rows),
        "plugins": rows,
        "limitations": [
            "This report is not a sandbox, security certification or proof of complete capability usage.",
            "Python static analysis cannot reliably resolve dynamic imports, reflection, aliases, native extensions or behavior in dependencies.",
            "A manifest permission is a declaration, not an authorization grant. Host policy and OS enforcement remain mandatory.",
        ],
    }


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Plugin Capability Inventory",
        "",
        f"**Plugins scanned:** {inventory['plugin_count']}",
        "",
        "> Static AST heuristic only. This is a migration aid, not a security certification or proof that a plugin cannot perform other operations.",
        "",
        "| Plugin | Declared permissions | Observed capabilities | Undeclared | Checksum | Warnings |",
        "|---|---|---|---|---|---|",
    ]
    for row in inventory["plugins"]:
        if "scan_error" in row:
            lines.append(f"| {row['plugin_name']} | — | scan error: {row['scan_error']} | — | — | — |")
            continue
        fmt = lambda values: ", ".join(values) if values else "—"
        lines.append(
            f"| {row['plugin_name']} | {fmt(row['declared_permissions'])} | "
            f"{fmt(row['observed_capabilities'])} | {fmt(row['undeclared_observed'])} | "
            f"{'match' if row['checksum_matches'] else 'MISMATCH'} | {fmt(row['warnings'])} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in inventory["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugins-dir", type=Path, default=Path("plugins"))
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inventory = build_inventory(args.plugins_dir)
    output = json.dumps(inventory, indent=2, ensure_ascii=False) if args.format == "json" else render_markdown(inventory)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
