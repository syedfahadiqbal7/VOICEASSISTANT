# ui/notify.py
"""Desktop notifications when the app is minimized / unfocused (Windows-first)."""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Optional


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def flash_taskbar(hwnd: int) -> None:
    """Flash the taskbar button until the user focuses the window."""
    if not _is_windows() or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        FLASHW_ALL = 3
        FLASHW_TIMERNOFG = 12
        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = hwnd
        info.dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG
        info.uCount = 3
        info.dwTimeout = 0
        user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def windows_toast(title: str, message: str) -> bool:
    """Show a Windows toast via PowerShell (no extra pip packages)."""
    if not _is_windows():
        return False
    title = (title or "VoiceAssistant")[:120]
    message = (message or "Response ready")[:240]

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    q = chr(34)
    xml = (
        "<toast><visual><binding template="
        + q
        + "ToastGeneric"
        + q
        + ">"
        + f"<text>{esc(title)}</text>"
        + f"<text>{esc(message)}</text>"
        + "</binding></visual></toast>"
    )
    apos = chr(39)
    ps = (
        "$ErrorActionPreference = " + apos + "Stop" + apos + "; " 
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; "
        f"$template = {apos}{xml}{apos}; "
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$xml.LoadXml($template); "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        + q
        + "VoiceAssistant"
        + q
        + ").Show($toast)"
    )
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            timeout=8,
            creationflags=flags,
        )
        return True
    except Exception:
        return False


def notify_background(
    title: str,
    message: str,
    *,
    hwnd: Optional[int] = None,
) -> None:
    """Non-blocking notify for background/minimized app."""

    def _run():
        try:
            windows_toast(title, message)
        except Exception:
            pass
        try:
            if hwnd:
                flash_taskbar(int(hwnd))
        except Exception:
            pass
        try:
            if _is_windows():
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()

