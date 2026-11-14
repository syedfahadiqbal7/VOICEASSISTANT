# plugins/web_search_inapp.py
"""In-app web search — text results in the agent (no browser window)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
from urllib.request import Request, urlopen

from tools.base import BaseTool


class WebSearchInAppTool(BaseTool):
    @property
    def name(self) -> str:
        return "web_search_inapp"

    @property
    def description(self) -> str:
        return (
            "Search the web and return text results inside the app (no browser). "
            "Use for 'voice search', 'search in app', or when user wants results in chat. "
            "For opening Google in a browser, use web_search_open instead."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max results (default 5, max 10)",
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str = "", max_results: int = 5, **kwargs) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"content": "Empty query", "is_error": True}
        try:
            max_results = max(1, min(int(max_results or 5), 10))
        except Exception:
            max_results = 5

        lines: List[str] = [f"In-app search results for: {q}", ""]
        errors: List[str] = []
        results: List[Dict[str, str]] = []

        # 1) DuckDuckGo Instant Answer
        try:
            ia = self._duckduckgo_ia(q)
            if ia.get("AbstractText"):
                lines.append(f"Summary: {ia['AbstractText'][:600]}")
                if ia.get("AbstractURL"):
                    lines.append(f"Source: {ia['AbstractURL']}")
                lines.append("")
            for item in ia.get("RelatedTopics") or []:
                if len(results) >= max_results:
                    break
                if isinstance(item, dict) and item.get("Text"):
                    results.append(
                        {
                            "title": item.get("Text", "")[:200],
                            "url": item.get("FirstURL") or "",
                            "snippet": "",
                        }
                    )
                elif isinstance(item, dict) and item.get("Topics"):
                    for sub in item.get("Topics") or []:
                        if len(results) >= max_results:
                            break
                        if isinstance(sub, dict) and sub.get("Text"):
                            results.append(
                                {
                                    "title": sub.get("Text", "")[:200],
                                    "url": sub.get("FirstURL") or "",
                                    "snippet": "",
                                }
                            )
        except Exception as e:
            errors.append(f"DDG-IA: {e}")

        # 2) DuckDuckGo HTML
        if len(results) < max_results:
            try:
                html_results = self._duckduckgo_html(q, max_results)
                for r in html_results:
                    if len(results) >= max_results:
                        break
                    # dedupe by url/title
                    key = (r.get("url") or r.get("title") or "").lower()
                    if key and any(
                        (x.get("url") or x.get("title") or "").lower() == key for x in results
                    ):
                        continue
                    results.append(r)
            except Exception as e:
                errors.append(f"DDG-HTML: {e}")

        # 3) DuckDuckGo lite
        if len(results) < 2:
            try:
                lite = self._duckduckgo_lite(q, max_results)
                for r in lite:
                    if len(results) >= max_results:
                        break
                    results.append(r)
            except Exception as e:
                errors.append(f"DDG-Lite: {e}")

        # 4) Wikipedia opensearch (always useful technical fallback)
        if len(results) < 2:
            try:
                wiki = self._wikipedia(q, max_results)
                for r in wiki:
                    if len(results) >= max_results:
                        break
                    results.append(r)
            except Exception as e:
                errors.append(f"Wikipedia: {e}")

        if results:
            for i, r in enumerate(results[:max_results], 1):
                title = (r.get("title") or "").strip() or "Result"
                lines.append(f"{i}. {title}")
                if r.get("url"):
                    lines.append(f"   {r['url']}")
                if r.get("snippet"):
                    lines.append(f"   {r['snippet'][:220]}")
            return {"content": "\n".join(lines), "is_error": False}

        lines.append("No structured results from search backends.")
        if errors:
            lines.append("Details: " + " | ".join(errors[:4]))
        lines.append("Fallback: say  search the web for " + q + "  (opens browser).")
        return {"content": "\n".join(lines), "is_error": False}

    def _get(self, url: str, timeout: float = 15.0) -> str:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/json,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    def _duckduckgo_ia(self, query: str) -> Dict[str, Any]:
        url = (
            "https://api.duckduckgo.com/?q="
            + quote_plus(query)
            + "&format=json&no_html=1&skip_disambig=1"
        )
        raw = self._get(url)
        return json.loads(raw)

    def _unwrap_ddg_href(self, href: str) -> str:
        href = href or ""
        # //duckduckgo.com/l/?uddg=...
        if "uddg=" in href:
            try:
                # may be relative
                if href.startswith("//"):
                    href = "https:" + href
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                if "uddg" in qs:
                    return unquote(qs["uddg"][0])
            except Exception:
                pass
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                return unquote(m.group(1))
        if href.startswith("//"):
            href = "https:" + href
        return href

    def _duckduckgo_html(self, query: str, limit: int) -> List[Dict[str, str]]:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        html = self._get(url)
        results: List[Dict[str, str]] = []

        # Primary: result__a + optional snippet nearby
        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            href = self._unwrap_ddg_href(m.group(1))
            title = re.sub(r"<[^>]+>", "", m.group(2))
            title = re.sub(r"\s+", " ", title).strip()
            # snippet after this match
            tail = html[m.end() : m.end() + 500]
            sm = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', tail, re.I | re.S)
            snip = ""
            if sm:
                snip = re.sub(r"<[^>]+>", "", sm.group(1))
                snip = re.sub(r"\s+", " ", snip).strip()
            if title:
                results.append({"title": title, "url": href, "snippet": snip})
            if len(results) >= limit:
                break
        return results

    def _duckduckgo_lite(self, query: str, limit: int) -> List[Dict[str, str]]:
        url = "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
        html = self._get(url)
        results: List[Dict[str, str]] = []
        # lite uses simple links in table
        for m in re.finditer(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>',
            html,
            re.I | re.S,
        ):
            href = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if "duckduckgo.com" in href:
                continue
            results.append({"title": title, "url": href, "snippet": ""})
            if len(results) >= limit:
                break
        if results:
            return results
        # even looser
        for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if "duckduckgo.com" in href or len(title) < 5:
                continue
            if href.startswith("https://duckduckgo.com"):
                continue
            results.append({"title": title[:200], "url": href, "snippet": ""})
            if len(results) >= limit:
                break
        return results

    def _wikipedia(self, query: str, limit: int) -> List[Dict[str, str]]:
        # MediaWiki opensearch
        url = (
            "https://en.wikipedia.org/w/api.php?action=opensearch&search="
            + quote_plus(query)
            + f"&limit={limit}&namespace=0&format=json"
        )
        raw = self._get(url)
        data = json.loads(raw)
        # [query, titles[], descriptions[], urls[]]
        titles = data[1] if len(data) > 1 else []
        descs = data[2] if len(data) > 2 else []
        urls = data[3] if len(data) > 3 else []
        out: List[Dict[str, str]] = []
        for i, title in enumerate(titles):
            out.append(
                {
                    "title": title,
                    "url": urls[i] if i < len(urls) else "",
                    "snippet": descs[i] if i < len(descs) else "",
                }
            )
        return out
