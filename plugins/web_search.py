# plugins/web_search.py
from tools.base import BaseTool
from typing import Any, Dict
import urllib.parse
import urllib.request
import re


class WebSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Perform a simple web search and return top results (DuckDuckGo)."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        }

    def execute(self, query: str) -> Dict[str, Any]:
        try:
            url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # Very simple extraction
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html)
            results = titles[:8]
            if not results:
                return {"content": "No results found or parsing failed.", "is_error": False}
            return {"content": "Top results:\n" + "\n".join(f"- {r}" for r in results), "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}