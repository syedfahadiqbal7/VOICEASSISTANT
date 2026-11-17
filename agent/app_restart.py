# agent/app_restart.py
"""Restart the VoiceAssistant process with an orderly desktop shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def restart_app(root_widget=None) -> bool:
    """Spawn the replacement process, then close the current UI through its shutdown hook."""
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
    for candidate in candidates:
        try:
            if candidate and candidate.is_file() and candidate.suffix.lower() == ".py":
                target = candidate.resolve()
                break
        except Exception:
            continue

    if target is None:
        raise RuntimeError("Could not locate main.py; restart was not attempted.")

    python = sys.executable or "python"
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(
            [python, str(target)],
            cwd=str(target.parent),
            close_fds=True,
            creationflags=flags,
        )
    else:
        subprocess.Popen(
            [python, str(target)],
            cwd=str(target.parent),
            close_fds=True,
            start_new_session=True,
        )

    if root_widget is not None:
        shutdown = getattr(root_widget, "_on_close", None)
        close = shutdown if callable(shutdown) else getattr(root_widget, "destroy", None)
        if not callable(close):
            raise RuntimeError("Replacement process started, but the current window has no shutdown callback.")
        try:
            root_widget.after(0, close)
        except Exception:
            # restart_app is called from the UI thread; invoke orderly shutdown directly
            # if Tk can no longer schedule an after callback.
            close()
        return True

    raise SystemExit(0)
