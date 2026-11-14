# plugins/browser_skill.py
"""Browser skill pack: open URL + web search in default browser."""

from __future__ import annotations

import webbrowser
from typing import Any, Dict
from urllib.parse import quote_plus

from tools.base import BaseTool


class BrowserOpenTool(BaseTool):
    @property
    def name(self) -> str:
        return "browser_open"

    @property
    def description(self) -> str:
        return (
            "Open a URL in the default browser. "
            "Use for http/https links. Example: https://github.com"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL including https://",
                }
            },
            "required": ["url"],
        }

    def execute(self, url: str = "", **kwargs) -> Dict[str, Any]:
        u = (url or "").strip()
        if not u:
            return {"content": "No URL provided", "is_error": True}
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        try:
            webbrowser.open(u)
            return {"content": f"Opened browser: {u}", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}


class WebSearchOpenTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search_open"

    @property
    def description(self) -> str:
        return (
            "Search the web in the default browser (Google). "
            "Use when user asks to search for something online."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "engine": {
                    "type": "string",
                    "description": "google (default) or bing or duckduckgo",
                    "enum": ["google", "bing", "duckduckgo"],
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str = "", engine: str = "google", **kwargs) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"content": "Empty search query", "is_error": True}
        eng = (engine or "google").lower()
        if eng == "bing":
            url = f"https://www.bing.com/search?q={quote_plus(q)}"
        elif eng in ("duckduckgo", "ddg"):
            url = f"https://duckduckgo.com/?q={quote_plus(q)}"
        else:
            url = f"https://www.google.com/search?q={quote_plus(q)}"
        try:
            webbrowser.open(url)
            return {"content": f"Opened {eng} search: {q}", "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}
