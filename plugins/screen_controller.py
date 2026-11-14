# plugins/screen_controller.py
"""
Professional Screen Controller Tool
-----------------------------------
Full desktop GUI automation using pyautogui + optional OCR.

Capabilities:
- Left / Right / Double / Middle click (by coordinates or by visible text)
- Move mouse
- Type text
- Press keys & hotkeys (Ctrl+C, Alt+Tab, etc.)
- Scroll
- Drag and drop
- Get current mouse position
- Get screen size
- Fail-safe enabled (move mouse to top-left corner to abort)

Requires:
    pip install pyautogui pillow pytesseract
    (Also install Tesseract-OCR on Windows for text-click feature)
"""

from __future__ import annotations

import time
import re
from typing import Any, Dict, List, Optional, Tuple

from tools.base import BaseTool


class ScreenControllerTool(BaseTool):
    @property
    def name(self) -> str:
        return "screen_controller"

    @property
    def description(self) -> str:
        return (
            "Control the mouse and keyboard on the user's Windows desktop. "
            "Use this tool to click buttons, links, videos, menu items, type text, "
            "press hotkeys, scroll pages, and interact with any visible UI. "
            "Prefer clicking by visible text when possible (action=click_text). "
            "Use coordinates only when text is not reliable. "
            "Examples: click the second YouTube video, type a search query, "
            "press Enter, scroll down, click OK button."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "The action to perform. One of: "
                        "click, double_click, right_click, middle_click, "
                        "click_text, move, type, press, hotkey, scroll, "
                        "drag, position, screen_size"
                    ),
                    "enum": [
                        "click",
                        "double_click",
                        "right_click",
                        "middle_click",
                        "click_text",
                        "move",
                        "type",
                        "press",
                        "hotkey",
                        "scroll",
                        "drag",
                        "position",
                        "screen_size",
                    ],
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate (pixels from left). Required for coordinate-based actions.",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate (pixels from top). Required for coordinate-based actions.",
                },
                "text": {
                    "type": "string",
                    "description": (
                        "For action=click_text: the visible text to find and click "
                        "(e.g. 'Second song title', 'Subscribe', 'OK'). "
                        "For action=type: the text to type on keyboard."
                    ),
                },
                "keys": {
                    "type": "string",
                    "description": (
                        "For action=press: a single key (e.g. 'enter', 'esc', 'tab', 'backspace'). "
                        "For action=hotkey: keys separated by '+' (e.g. 'ctrl+c', 'alt+tab', 'ctrl+shift+esc')."
                    ),
                },
                "clicks": {
                    "type": "integer",
                    "description": "Number of clicks for action=click (default 1).",
                },
                "button": {
                    "type": "string",
                    "description": "Mouse button for action=click: left, right, or middle (default left).",
                },
                "scroll_amount": {
                    "type": "integer",
                    "description": (
                        "For action=scroll: positive = scroll up, negative = scroll down. "
                        "Typical values: 3 to 10."
                    ),
                },
                "end_x": {
                    "type": "integer",
                    "description": "End X for action=drag.",
                },
                "end_y": {
                    "type": "integer",
                    "description": "End Y for action=drag.",
                },
                "duration": {
                    "type": "number",
                    "description": "Seconds to move/drag (default 0.25). Makes movement smoother.",
                },
                "interval": {
                    "type": "number",
                    "description": "Delay between keystrokes when typing (default 0.02).",
                },
                "match_index": {
                    "type": "integer",
                    "description": (
                        "When multiple matches found for click_text, which one to click "
                        "(1 = first, 2 = second, etc.). Default 1."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": "OCR match looseness is automatic; this is reserved for future use.",
                },
            },
            "required": ["action"],
        }

    @property
    def help(self) -> Dict[str, str]:
        return {
            "summary": "Full mouse + keyboard control of the Windows desktop (click, type, hotkeys, scroll, drag).",
            "usage": (
                "screen_controller(action=..., ...)\n"
                "Examples:\n"
                "  action=click_text, text='Play'\n"
                "  action=click_text, text='Tumse Milne', match_index=2\n"
                "  action=click, x=500, y=300\n"
                "  action=type, text='hello world'\n"
                "  action=hotkey, keys='ctrl+enter'\n"
                "  action=scroll, scroll_amount=-5\n"
                "  action=press, keys='enter'"
            ),
            "examples": (
                "Click second YouTube result → action=click_text, text=<part of title>, match_index=2\n"
                "Type in search box → action=type, text=...\n"
                "Press Enter → action=press, keys=enter\n"
                "Scroll down → action=scroll, scroll_amount=-8"
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_pyautogui(self):
        try:
            import pyautogui
        except ImportError as e:
            raise RuntimeError(
                "pyautogui is not installed. Run: pip install pyautogui pillow"
            ) from e
        # Safety: moving mouse to top-left aborts
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        return pyautogui

    def _screen_size(self) -> Tuple[int, int]:
        pag = self._ensure_pyautogui()
        return pag.size()

    def _validate_coords(self, x: int, y: int) -> None:
        w, h = self._screen_size()
        if x < 0 or y < 0 or x >= w or y >= h:
            raise ValueError(
                f"Coordinates ({x},{y}) are outside screen bounds ({w}x{h})."
            )

    def _find_text_boxes(self, query: str) -> List[Dict[str, Any]]:
        """
        Take a screenshot and find all OCR bounding boxes whose text
        contains the query (case-insensitive).
        Returns list of {text, left, top, width, height, conf, center_x, center_y}
        sorted top-to-bottom, left-to-right.
        """
        try:
            from PIL import ImageGrab
            import pytesseract
        except ImportError as e:
            raise RuntimeError(
                "OCR dependencies missing. Run: pip install pillow pytesseract\n"
                "Also install Tesseract-OCR for Windows from:\n"
                "https://github.com/UB-Mannheim/tesseract/wiki"
            ) from e

        img = ImageGrab.grab()
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        query_lower = query.strip().lower()
        if not query_lower:
            return []

        results = []
        n = len(data["text"])
        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            conf = int(float(data["conf"][i])) if data["conf"][i] != "-1" else 0
            if conf < 40:
                continue

            # Also try combining with next few words for multi-word matches
            combined = word
            left = data["left"][i]
            top = data["top"][i]
            width = data["width"][i]
            height = data["height"][i]

            # Single word match
            candidates = [(combined, left, top, width, height, conf)]

            # Multi-word: join up to 6 following tokens on same line
            for j in range(1, 7):
                if i + j >= n:
                    break
                if data["top"][i + j] != top:  # different line
                    break
                next_word = (data["text"][i + j] or "").strip()
                if not next_word:
                    continue
                combined = combined + " " + next_word
                width = (data["left"][i + j] + data["width"][i + j]) - left
                conf = min(conf, int(float(data["conf"][i + j])) if data["conf"][i + j] != "-1" else conf)
                candidates.append((combined, left, top, width, height, conf))

            for text, l, t, w, h, c in candidates:
                if query_lower in text.lower():
                    results.append(
                        {
                            "text": text,
                            "left": l,
                            "top": t,
                            "width": w,
                            "height": h,
                            "conf": c,
                            "center_x": l + w // 2,
                            "center_y": t + h // 2,
                        }
                    )

        # Deduplicate near-identical boxes
        unique = []
        for r in results:
            if any(
                abs(r["center_x"] - u["center_x"]) < 15
                and abs(r["center_y"] - u["center_y"]) < 15
                for u in unique
            ):
                continue
            unique.append(r)

        # Reading order
        unique.sort(key=lambda b: (b["top"] // 20, b["left"]))
        return unique

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    def _action_click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        duration: float = 0.15,
    ) -> str:
        pag = self._ensure_pyautogui()
        self._validate_coords(x, y)
        button = (button or "left").lower()
        if button not in ("left", "right", "middle"):
            button = "left"
        clicks = max(1, int(clicks or 1))
        pag.moveTo(x, y, duration=max(0.05, float(duration or 0.15)))
        pag.click(x=x, y=y, clicks=clicks, button=button)
        return f"Clicked ({button}, {clicks}x) at ({x}, {y})"

    def _action_double_click(self, x: int, y: int, duration: float = 0.15) -> str:
        return self._action_click(x, y, button="left", clicks=2, duration=duration)

    def _action_right_click(self, x: int, y: int, duration: float = 0.15) -> str:
        return self._action_click(x, y, button="right", clicks=1, duration=duration)

    def _action_middle_click(self, x: int, y: int, duration: float = 0.15) -> str:
        return self._action_click(x, y, button="middle", clicks=1, duration=duration)

    def _action_click_text(
        self,
        text: str,
        match_index: int = 1,
        button: str = "left",
        clicks: int = 1,
    ) -> str:
        if not text or not text.strip():
            raise ValueError("text is required for click_text")

        matches = self._find_text_boxes(text.strip())
        if not matches:
            raise RuntimeError(
                f"Could not find visible text matching '{text}' on screen. "
                "Make sure the text is visible and Tesseract OCR is installed."
            )

        idx = max(1, int(match_index or 1))
        if idx > len(matches):
            preview = ", ".join(f'"{m["text"]}"' for m in matches[:5])
            raise RuntimeError(
                f"Only found {len(matches)} match(es) for '{text}'. "
                f"Requested index {idx}. Found: {preview}"
            )

        target = matches[idx - 1]
        x, y = target["center_x"], target["center_y"]
        msg = self._action_click(x, y, button=button or "left", clicks=clicks or 1)
        return (
            f"{msg}\n"
            f"Matched text: \"{target['text']}\" "
            f"(match {idx}/{len(matches)}, conf={target['conf']})"
        )

    def _action_move(self, x: int, y: int, duration: float = 0.25) -> str:
        pag = self._ensure_pyautogui()
        self._validate_coords(x, y)
        pag.moveTo(x, y, duration=max(0.05, float(duration or 0.25)))
        return f"Moved mouse to ({x}, {y})"

    def _action_type(self, text: str, interval: float = 0.02) -> str:
        if text is None:
            raise ValueError("text is required for type action")
        pag = self._ensure_pyautogui()
        # Use write for unicode support; fallback to typewrite
        try:
            pag.write(text, interval=max(0.0, float(interval or 0.02)))
        except Exception:
            pag.typewrite(text, interval=max(0.0, float(interval or 0.02)))
        return f"Typed {len(text)} character(s)"

    def _action_press(self, keys: str) -> str:
        if not keys:
            raise ValueError("keys is required for press action")
        pag = self._ensure_pyautogui()
        key = keys.strip().lower()
        pag.press(key)
        return f"Pressed key: {key}"

    def _action_hotkey(self, keys: str) -> str:
        if not keys:
            raise ValueError("keys is required for hotkey action (e.g. ctrl+c)")
        pag = self._ensure_pyautogui()
        parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
        if not parts:
            raise ValueError(f"Invalid hotkey: {keys}")
        pag.hotkey(*parts)
        return f"Hotkey pressed: {'+'.join(parts)}"

    def _action_scroll(self, scroll_amount: int = -3, x: Optional[int] = None, y: Optional[int] = None) -> str:
        pag = self._ensure_pyautogui()
        amount = int(scroll_amount if scroll_amount is not None else -3)
        if x is not None and y is not None:
            self._validate_coords(int(x), int(y))
            pag.moveTo(int(x), int(y), duration=0.1)
        pag.scroll(amount)
        direction = "up" if amount > 0 else "down"
        return f"Scrolled {direction} by {abs(amount)} unit(s)"

    def _action_drag(
        self,
        x: int,
        y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.4,
        button: str = "left",
    ) -> str:
        pag = self._ensure_pyautogui()
        self._validate_coords(x, y)
        self._validate_coords(end_x, end_y)
        pag.moveTo(x, y, duration=0.1)
        pag.drag(
            end_x - x,
            end_y - y,
            duration=max(0.1, float(duration or 0.4)),
            button=(button or "left"),
        )
        return f"Dragged from ({x},{y}) to ({end_x},{end_y})"

    def _action_position(self) -> str:
        pag = self._ensure_pyautogui()
        pos = pag.position()
        return f"Mouse position: x={pos.x}, y={pos.y}"

    def _action_screen_size(self) -> str:
        w, h = self._screen_size()
        return f"Screen size: {w} x {h} pixels"

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def execute(
        self,
        action: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        text: Optional[str] = None,
        keys: Optional[str] = None,
        clicks: Optional[int] = None,
        button: Optional[str] = None,
        scroll_amount: Optional[int] = None,
        end_x: Optional[int] = None,
        end_y: Optional[int] = None,
        duration: Optional[float] = None,
        interval: Optional[float] = None,
        match_index: Optional[int] = None,
        confidence: Optional[float] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            action = (action or "").strip().lower()
            if not action:
                return {"content": "action is required", "is_error": True}

            # Small settle delay so UI can finish rendering
            time.sleep(0.15)

            if action == "click":
                if x is None or y is None:
                    return {"content": "x and y are required for click", "is_error": True}
                msg = self._action_click(int(x), int(y), button=button or "left", clicks=clicks or 1, duration=duration or 0.15)

            elif action == "double_click":
                if x is None or y is None:
                    return {"content": "x and y are required for double_click", "is_error": True}
                msg = self._action_double_click(int(x), int(y), duration=duration or 0.15)

            elif action == "right_click":
                if x is None or y is None:
                    return {"content": "x and y are required for right_click", "is_error": True}
                msg = self._action_right_click(int(x), int(y), duration=duration or 0.15)

            elif action == "middle_click":
                if x is None or y is None:
                    return {"content": "x and y are required for middle_click", "is_error": True}
                msg = self._action_middle_click(int(x), int(y), duration=duration or 0.15)

            elif action == "click_text":
                msg = self._action_click_text(
                    text=text or "",
                    match_index=match_index or 1,
                    button=button or "left",
                    clicks=clicks or 1,
                )

            elif action == "move":
                if x is None or y is None:
                    return {"content": "x and y are required for move", "is_error": True}
                msg = self._action_move(int(x), int(y), duration=duration or 0.25)

            elif action == "type":
                msg = self._action_type(text if text is not None else "", interval=interval or 0.02)

            elif action == "press":
                msg = self._action_press(keys or "")

            elif action == "hotkey":
                msg = self._action_hotkey(keys or "")

            elif action == "scroll":
                msg = self._action_scroll(
                    scroll_amount=scroll_amount if scroll_amount is not None else -3,
                    x=x,
                    y=y,
                )

            elif action == "drag":
                if None in (x, y, end_x, end_y):
                    return {"content": "x, y, end_x, end_y are required for drag", "is_error": True}
                msg = self._action_drag(
                    int(x), int(y), int(end_x), int(end_y),
                    duration=duration or 0.4,
                    button=button or "left",
                )

            elif action == "position":
                msg = self._action_position()

            elif action == "screen_size":
                msg = self._action_screen_size()

            else:
                return {
                    "content": f"Unknown action '{action}'. "
                    "Valid: click, double_click, right_click, middle_click, click_text, "
                    "move, type, press, hotkey, scroll, drag, position, screen_size",
                    "is_error": True,
                }

            return {"content": msg, "is_error": False}

        except Exception as e:
            return {"content": f"screen_controller error: {e}", "is_error": True}
