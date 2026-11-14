# agent/verifier.py
"""
Phase 1 — Post-action Verifier
Checks whether a plan step actually succeeded on the real system.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional


class Verifier:
    def verify(
        self,
        success_check: Optional[Dict[str, Any]],
        tool_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        check = success_check or {"type": "always_true"}
        ctype = (check.get("type") or "always_true").strip().lower()

        try:
            if ctype == "always_true":
                if tool_result and tool_result.get("is_error"):
                    return {
                        "ok": False,
                        "message": f"Tool reported error: {tool_result.get('content')}",
                        "check_type": ctype,
                    }
                return {
                    "ok": True,
                    "message": "No post-condition (always_true)",
                    "check_type": ctype,
                }

            if ctype == "file_exists":
                path = self._expand(check.get("path", ""))
                if not path:
                    return {
                        "ok": False,
                        "message": "file_exists missing path",
                        "check_type": ctype,
                    }
                ok = self._wait(lambda: os.path.isfile(path), timeout=3.0)
                return {
                    "ok": ok,
                    "message": f"File exists: {path}" if ok else f"File NOT found: {path}",
                    "check_type": ctype,
                }

            if ctype == "file_contains":
                path = self._expand(check.get("path", ""))
                needle = check.get("text", "")
                if not path:
                    return {
                        "ok": False,
                        "message": "file_contains missing path",
                        "check_type": ctype,
                    }
                if not os.path.isfile(path):
                    return {
                        "ok": False,
                        "message": f"File not found for contains-check: {path}",
                        "check_type": ctype,
                    }
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        data = f.read()
                    ok = str(needle) in data
                    return {
                        "ok": ok,
                        "message": (
                            f"File contains expected text ({len(str(needle))} chars)"
                            if ok
                            else f"File does not contain expected text: {path}"
                        ),
                        "check_type": ctype,
                    }
                except Exception as e:
                    return {
                        "ok": False,
                        "message": f"Read failed: {e}",
                        "check_type": ctype,
                    }

            if ctype == "dir_exists":
                path = self._expand(check.get("path", ""))
                ok = bool(path) and os.path.isdir(path)
                return {
                    "ok": ok,
                    "message": (
                        f"Directory exists: {path}" if ok else f"Directory NOT found: {path}"
                    ),
                    "check_type": ctype,
                }

            if ctype == "process_running":
                name = (check.get("name") or check.get("process") or "").lower()
                if not name:
                    return {
                        "ok": False,
                        "message": "process_running missing name",
                        "check_type": ctype,
                    }
                ok = self._process_running(name)
                return {
                    "ok": ok,
                    "message": (
                        f"Process running: {name}" if ok else f"Process NOT running: {name}"
                    ),
                    "check_type": ctype,
                }

            if ctype == "window_title_contains":
                text = check.get("text") or check.get("title") or ""
                if not text:
                    return {
                        "ok": False,
                        "message": "window_title_contains missing text",
                        "check_type": ctype,
                    }
                try:
                    import pygetwindow as gw

                    titles = [t for t in gw.getAllTitles() if t]
                    ok = any(text.lower() in (t or "").lower() for t in titles)
                    return {
                        "ok": ok,
                        "message": (
                            f"Found window containing '{text}'"
                            if ok
                            else f"No window title contains '{text}'"
                        ),
                        "check_type": ctype,
                    }
                except Exception as e:
                    return {
                        "ok": False,
                        "message": (
                            f"window_title_contains could not be verified: {e}. "
                            "Install/enable the required window-inspection capability and retry."
                        ),
                        "check_type": ctype,
                        "verifiable": False,
                    }

            if ctype == "command_exit_zero":
                if not isinstance(tool_result, dict):
                    return {"ok": False, "message": "Missing command result", "check_type": ctype}
                if tool_result.get("is_error"):
                    return {
                        "ok": False,
                        "message": f"Command failed: {tool_result.get('content')}",
                        "check_type": ctype,
                    }
                if "exit_code" not in tool_result:
                    return {
                        "ok": False,
                        "message": "Command result did not include an exit code; success cannot be verified",
                        "check_type": ctype,
                    }
                exit_code = tool_result.get("exit_code")
                ok = isinstance(exit_code, int) and exit_code == 0
                return {
                    "ok": ok,
                    "message": f"Command exit code: {exit_code}",
                    "check_type": ctype,
                }

            return {
                "ok": False,
                "message": f"Unknown success_check type: {ctype}",
                "check_type": ctype,
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"Verifier exception: {e}",
                "check_type": ctype,
            }

    def _expand(self, path: str) -> str:
        if not path:
            return ""
        path = path.replace("\\", "/")
        home = os.path.expanduser("~").replace("\\", "/")
        for p in ("YourUsername", "YourName", "username", "YourUserName"):
            if p in path:
                path = path.replace(f"C:/Users/{p}", home).replace(
                    p, os.path.basename(home)
                )
        if path.lower().startswith("desktop/"):
            path = f"{home}/Desktop/{path.split('/', 1)[-1]}"
        return os.path.expandvars(path)

    def _wait(self, pred, timeout: float = 3.0, interval: float = 0.2) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if pred():
                    return True
            except Exception:
                pass
            time.sleep(interval)
        try:
            return bool(pred())
        except Exception:
            return False

    def _process_running(self, name: str) -> bool:
        name = name.lower().replace(".exe", "")
        try:
            import psutil

            for proc in psutil.process_iter(["name"]):
                n = (proc.info.get("name") or "").lower().replace(".exe", "")
                if name in n:
                    return True
            return False
        except Exception:
            try:
                import subprocess

                out = subprocess.check_output(
                    ["tasklist"], text=True, stderr=subprocess.DEVNULL, timeout=5
                ).lower()
                return name in out
            except Exception:
                return False
