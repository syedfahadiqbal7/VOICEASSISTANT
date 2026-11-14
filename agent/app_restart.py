# agent/app_restart.py
"""Restart the VoiceAssistant process (Windows-friendly)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def restart_app(root_widget=None) -> None:
    """
    Spawn a new python main.py (or current script) then exit.
    Call from UI after plugin/skill install when user confirms.
    """
    # Prefer project main.py
    candidates = []
    try:
        from config import BASE_DIR
        candidates.append(Path(BASE_DIR) / "main.py")
    except Exception:
        pass
    candidates.append(Path.cwd() / "main.py")
    if getattr(sys, "argv", None) and sys.argv:
        candidates.append(Path(sys.argv[0]).resolve())

    target: Optional[Path] = None
    for c in candidates:
        try:
            if c and c.is_file() and c.suffix.lower() == ".py":
                target = c
                break
        except Exception:
            continue

    python = sys.executable or "python"
    try:
        if target:
            # Detach on Windows
            if os.name == "nt":
                import subprocess
                subprocess.Popen(
                    [python, str(target)],
                    cwd=str(target.parent),
                    close_fds=True,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            else:
                import subprocess
                subprocess.Popen([python, str(target)], cwd=str(target.parent), start_new_session=True)
    except Exception as e:
        print(f"[Restart] spawn failed: {e}")

    # Close UI then exit
    try:
        if root_widget is not None:
            root_widget.after(200, root_widget.destroy)
    except Exception:
        pass
    try:
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        os._exit(0)
