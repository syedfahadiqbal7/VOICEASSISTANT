# plugins/open_app.py
"""
Open URL or launch a Windows app by friendly name / exe / path.
Resolves installed apps via PATH, App Paths registry, Start Menu .lnk, and Program Files search.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from tools.base import BaseTool

# Optional: pywin32 for shortcut resolution
try:
    import win32com.client  # type: ignore

    _HAS_WIN32 = True
except Exception:
    _HAS_WIN32 = False


class OpenAppTool(BaseTool):
    @property
    def name(self) -> str:
        return "open_app"

    @property
    def description(self) -> str:
        return (
            "Open a website URL or launch a Windows application by name. "
            "Pass 'target' as a URL, exe name, or friendly app name "
            "(e.g. 'Visual Studio', 'Chrome', 'notepad'). "
            "The tool searches PATH, registry App Paths, Start Menu shortcuts, and Program Files."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "URL or application name/path to open",
                }
            },
            "required": ["target"],
        }

    def execute(self, target: str = "", **kwargs) -> Dict[str, Any]:
        if not target and kwargs.get("app_name"):
            target = str(kwargs.get("app_name"))
        target = (target or "").strip().strip('"')
        if not target:
            return {"content": "No target specified", "is_error": True}

        try:
            if target.startswith("http://") or target.startswith("https://"):
                webbrowser.open(target)
                return {"content": f"Opened URL: {target}", "is_error": False}

            resolved = self._resolve(target)
            if not resolved:
                return {
                    "content": (
                        f"Could not find installed app matching '{target}'. "
                        "Try the exact .exe name or full path."
                    ),
                    "is_error": True,
                }

            os.startfile(resolved)  # type: ignore[attr-defined]
            label = resolved if resolved.lower() != target.lower() else target
            return {"content": f"Launched: {label}", "is_error": False}
        except Exception as e:
            return {"content": f"Failed to open '{target}': {e}", "is_error": True}

    # ------------------------------------------------------------------
    def _resolve(self, target: str) -> Optional[str]:
        """Return a path or shell name that os.startfile can launch."""
        t = target.strip().strip('"')
        # 1) Exact path
        if os.path.isfile(t):
            return t

        # 2) PATH / where
        which = shutil.which(t)
        if which and os.path.isfile(which):
            return which
        if not t.lower().endswith(".exe"):
            which = shutil.which(t + ".exe")
            if which and os.path.isfile(which):
                return which

        # 3) Registry App Paths (HKLM / HKCU)
        for name in self._name_variants(t):
            reg = self._registry_app_path(name)
            if reg and os.path.isfile(reg):
                return reg

        # 4) Start Menu shortcuts (.lnk) — match by display name
        lnk = self._find_start_menu_shortcut(t)
        if lnk:
            return lnk

        # 5) Program Files scan for *.exe name match (shallow + one level)
        found = self._scan_program_files(t)
        if found:
            return found

        # 6) Last resort: let Windows shell try the bare name
        # (works for notepad, calc, etc. via App Paths)
        if re.match(r"^[\w.\- ]+$", t):
            return t if t.lower().endswith(".exe") else t

        return None

    def _name_variants(self, t: str) -> List[str]:
        base = t.strip()
        out = [base, base + ".exe"]
        compact = re.sub(r"\s+", "", base)
        if compact != base:
            out.extend([compact, compact + ".exe"])
        # common friendly → exe hints (optional boost only; search still primary)
        hints = {
            "visual studio": "devenv.exe",
            "visualstudio": "devenv.exe",
            "vs": "devenv.exe",
            "visual studio code": "Code.exe",
            "vscode": "Code.exe",
            "vs code": "Code.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "notepad": "notepad.exe",
            "edge": "msedge.exe",
            "chrome": "chrome.exe",
            "word": "WINWORD.EXE",
            "excel": "EXCEL.EXE",
            "powerpoint": "POWERPNT.EXE",
            "outlook": "OUTLOOK.EXE",
            "explorer": "explorer.exe",
            "cmd": "cmd.exe",
            "powershell": "powershell.exe",
            "paint": "mspaint.exe",
        }
        key = re.sub(r"\s+", " ", base.lower())
        if key in hints:
            out.insert(0, hints[key])
        # dedupe
        seen: Set[str] = set()
        uniq: List[str] = []
        for x in out:
            k = x.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        return uniq

    def _registry_app_path(self, exe_name: str) -> Optional[str]:
        if os.name != "nt":
            return None
        try:
            import winreg  # type: ignore
        except Exception:
            return None

        name = exe_name if exe_name.lower().endswith(".exe") else exe_name + ".exe"
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{name}",
            ),
        ]
        for root, sub in keys:
            try:
                with winreg.OpenKey(root, sub) as k:
                    val, _ = winreg.QueryValueEx(k, None)
                    if val:
                        path = os.path.expandvars(str(val)).strip('"')
                        if os.path.isfile(path):
                            return path
            except OSError:
                continue
        return None

    def _iter_start_menu_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        for env in ("PROGRAMDATA", "APPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            p = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if p.is_dir():
                dirs.append(p)
        return dirs

    def _resolve_lnk(self, lnk_path: str) -> Optional[str]:
        if not _HAS_WIN32:
            # Without pywin32, launching the .lnk via startfile still works on Windows
            return lnk_path if os.path.isfile(lnk_path) else None
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(lnk_path)
            target = str(shortcut.Targetpath or "").strip()
            if target and os.path.isfile(target):
                return target
            # Still return lnk — startfile can open shortcuts
            return lnk_path
        except Exception:
            return lnk_path if os.path.isfile(lnk_path) else None

    def _find_start_menu_shortcut(self, query: str) -> Optional[str]:
        q = re.sub(r"\s+", " ", query.strip().lower())
        if not q:
            return None
        tokens = [t for t in re.split(r"[\s_\-]+", q) if t and t not in ("the", "app")]
        best: Optional[tuple] = None  # (score, path)

        for root in self._iter_start_menu_dirs():
            try:
                for lnk in root.rglob("*.lnk"):
                    name = lnk.stem.lower()
                    # score: exact > all tokens > substring
                    score = 0
                    if name == q or name.replace(" ", "") == q.replace(" ", ""):
                        score = 100
                    elif q in name:
                        score = 80
                    elif all(tok in name for tok in tokens):
                        score = 60 + min(20, 5 * len(tokens))
                    elif any(tok in name for tok in tokens if len(tok) > 2):
                        score = 30
                    else:
                        continue
                    if best is None or score > best[0]:
                        best = (score, str(lnk))
                        if score >= 100:
                            break
            except Exception:
                continue
            if best and best[0] >= 100:
                break

        if not best or best[0] < 30:
            return None
        return self._resolve_lnk(best[1])

    def _scan_program_files(self, query: str) -> Optional[str]:
        q = query.strip().lower()
        q_exe = q if q.endswith(".exe") else q + ".exe"
        q_compact = re.sub(r"[\s_\-]+", "", q)

        roots = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            v = os.environ.get(env)
            if v:
                roots.append(Path(v))
        # VS Code default
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "Programs")

        # Prefer exact exe name match at depth <= 4
        matches: List[str] = []
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    # prune deep trees
                    depth = Path(dirpath).relative_to(root).parts
                    if len(depth) > 5:
                        dirnames[:] = []
                        continue
                    for fn in filenames:
                        if not fn.lower().endswith(".exe"):
                            continue
                        fl = fn.lower()
                        if fl == q_exe or fl == q:
                            matches.append(str(Path(dirpath) / fn))
                        elif q_compact and q_compact in re.sub(r"[\s_\-]+", "", fl):
                            matches.append(str(Path(dirpath) / fn))
                    if len(matches) >= 8:
                        break
            except Exception:
                continue
            if len(matches) >= 8:
                break

        if not matches:
            return None
        # Prefer shorter path / exact name
        matches.sort(key=lambda p: (0 if Path(p).name.lower() in (q, q_exe) else 1, len(p)))
        return matches[0]
