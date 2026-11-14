# plugins/list_installed_apps.py
"""List installed Windows applications (Start Menu + Uninstall registry)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Set

from tools.base import BaseTool


class ListInstalledAppsTool(BaseTool):
    @property
    def name(self) -> str:
        return "list_installed_apps"

    @property
    def description(self) -> str:
        return (
            "List applications installed on this Windows PC "
            "(Start Menu shortcuts and Programs and Features registry). "
            "Use when user asks what apps are installed. "
            "Default returns up to 150 names; pass higher limit or a filter. "
            "Always prefer a large limit (100-200) unless the user asks for a short list."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional name filter (e.g. 'office', 'visual', 'adobe')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max names to return (default 150, max 400)",
                },
            },
            "required": [],
        }

    def execute(self, filter: str = "", limit: int = 150, **kwargs) -> Dict[str, Any]:
        try:
            names: Set[str] = set()
            filt = (filter or "").strip().lower()

            # Start Menu
            for env in ("PROGRAMDATA", "APPDATA"):
                base = os.environ.get(env)
                if not base:
                    continue
                root = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                if not root.is_dir():
                    continue
                try:
                    for lnk in root.rglob("*.lnk"):
                        n = lnk.stem.strip()
                        if n and not n.startswith("uninstall"):
                            names.add(n)
                except Exception:
                    pass

            # Uninstall registry display names
            try:
                import winreg  # type: ignore

                roots = [
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                    (
                        winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                    ),
                    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                ]
                for hive, path in roots:
                    try:
                        with winreg.OpenKey(hive, path) as key:
                            i = 0
                            while True:
                                try:
                                    sub = winreg.EnumKey(key, i)
                                    i += 1
                                except OSError:
                                    break
                                try:
                                    with winreg.OpenKey(key, sub) as sk:
                                        try:
                                            name, _ = winreg.QueryValueEx(sk, "DisplayName")
                                        except OSError:
                                            continue
                                        if name:
                                            names.add(str(name).strip())
                                except OSError:
                                    continue
                    except OSError:
                        continue
            except Exception:
                pass

            items = sorted(names, key=lambda s: s.lower())
            if filt:
                items = [x for x in items if filt in x.lower()]

            total = len(items)
            # Model often passes limit=10 — treat tiny limits as default unless intentional
            try:
                lim = int(limit) if limit is not None else 150
            except Exception:
                lim = 150
            # Allow explicit small limits (user said "limit 10").
            # Only coerce absurd 0/negative.
            if lim < 1:
                lim = 150
            lim = min(lim, 400)

            shown = items[:lim]
            if not shown:
                return {
                    "content": "No installed applications found (or filter matched nothing).",
                    "is_error": False,
                }

            body = "\n".join(f"- {x}" for x in shown)
            more = ""
            if total > len(shown):
                more = (
                    f"\n\n… and {total - len(shown)} more. "
                    f"Ask e.g. \"list installed apps filter visual\" or "
                    f"\"list installed apps limit 300\"."
                )
            return {
                "content": (
                    f"Installed applications (showing {len(shown)} of {total}"
                    f"{f', filter={filt!r}' if filt else ''}):\n{body}{more}"
                ),
                "is_error": False,
            }
        except Exception as e:
            return {"content": str(e), "is_error": True}
