# plugins/youtube_player.py
"""
Professional YouTube Player Tool
--------------------------------
Search YouTube and open the best matching video directly in the browser.

Uses yt-dlp for reliable search (no API key needed).
Fallback: opens YouTube search results page if yt-dlp is unavailable.

Install:
    pip install yt-dlp

Usage examples:
    - Play Tumse Milne Ko Dil Karta Hai
    - Play the 2nd result for lo-fi hip hop
    - Search only (don't autoplay): action=search
"""

from __future__ import annotations

import re
import shutil
import subprocess
import webbrowser
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from tools.base import BaseTool


class YouTubePlayerTool(BaseTool):
    @property
    def name(self) -> str:
        return "youtube_player"

    @property
    def description(self) -> str:
        return (
            "Search YouTube and play a video directly in the default browser. "
            "Use this whenever the user asks to play a song, video, music, or "
            "watch something on YouTube. "
            "Pass the song/video name in 'query'. "
            "Use 'index' to pick 1st, 2nd, 3rd result (default 1 = best match). "
            "Set action=search to only open the search page without auto-playing."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Song name, artist, or video title to search on YouTube. "
                        "Example: 'Tumse Milne Ko Dil Karta Hai', 'lofi hip hop', "
                        "'Python tutorial for beginners'"
                    ),
                },
                "index": {
                    "type": "integer",
                    "description": (
                        "Which search result to play. 1 = first/best match (default), "
                        "2 = second result, etc. Max 10."
                    ),
                },
                "action": {
                    "type": "string",
                    "description": (
                        "play = search and open the video (default). "
                        "search = only open YouTube search results page."
                    ),
                    "enum": ["play", "search"],
                },
            },
            "required": ["query"],
        }

    @property
    def help(self) -> Dict[str, str]:
        return {
            "summary": "Search YouTube and auto-play the matching video in the browser.",
            "usage": (
                "youtube_player(query='song name')\n"
                "youtube_player(query='song name', index=2)\n"
                "youtube_player(query='song name', action='search')"
            ),
            "examples": (
                "Play Kesariya → query='Kesariya Arijit Singh'\n"
                "Play 2nd result for lofi → query='lofi hip hop', index=2\n"
                "Only show search results → query='python async', action='search'"
            ),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_ytdlp(self) -> bool:
        return shutil.which("yt-dlp") is not None or self._can_import_yt_dlp()

    def _can_import_yt_dlp(self) -> bool:
        try:
            import yt_dlp  # noqa: F401
            return True
        except ImportError:
            return False

    def _search_with_ytdlp(self, query: str, max_results: int = 10) -> List[Dict[str, str]]:
        """
        Returns list of {id, title, url, channel} using yt-dlp.
        """
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "default_search": "ytsearch",
        }

        search_term = f"ytsearch{max_results}:{query}"
        results: List[Dict[str, str]] = []

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_term, download=False)

        entries = []
        if not info:
            return results
        if "entries" in info:
            entries = [e for e in info["entries"] if e]
        else:
            entries = [info]

        for e in entries:
            vid = e.get("id") or e.get("url")
            if not vid:
                continue
            # flat search sometimes returns url as id
            if "youtube.com" in str(vid) or "youtu.be" in str(vid):
                url = str(vid)
                # try extract id
                m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
                vid_id = m.group(1) if m else vid
            else:
                vid_id = str(vid)
                url = f"https://www.youtube.com/watch?v={vid_id}"

            results.append(
                {
                    "id": str(vid_id),
                    "title": e.get("title") or "Unknown title",
                    "url": url,
                    "channel": e.get("channel") or e.get("uploader") or "",
                }
            )
        return results

    def _search_url(self, query: str) -> str:
        return f"https://www.youtube.com/results?search_query={quote_plus(query)}"

    def _open(self, url: str) -> None:
        webbrowser.open(url)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def execute(
        self,
        query: str,
        index: int = 1,
        action: str = "play",
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            query = (query or "").strip()
            if not query:
                return {"content": "query is required (song or video name)", "is_error": True}

            action = (action or "play").strip().lower()
            if action not in ("play", "search"):
                action = "play"

            index = int(index or 1)
            if index < 1:
                index = 1
            if index > 10:
                index = 10

            # --- search only ---
            if action == "search":
                url = self._search_url(query)
                self._open(url)
                return {
                    "content": f"Opened YouTube search for: {query}\n{url}",
                    "is_error": False,
                }

            # --- play (pro path) ---
            if not self._has_ytdlp():
                # Graceful fallback
                url = self._search_url(query)
                self._open(url)
                return {
                    "content": (
                        f"yt-dlp not installed — opened search page instead.\n"
                        f"Query: {query}\n"
                        f"URL: {url}\n\n"
                        f"For auto-play install: pip install yt-dlp"
                    ),
                    "is_error": False,
                }

            results = self._search_with_ytdlp(query, max_results=max(index, 5))
            if not results:
                url = self._search_url(query)
                self._open(url)
                return {
                    "content": (
                        f"No results from yt-dlp for '{query}'. "
                        f"Opened search page instead.\n{url}"
                    ),
                    "is_error": False,
                }

            if index > len(results):
                index = len(results)

            chosen = results[index - 1]
            self._open(chosen["url"])

            # Build a short list of top matches for transparency
            preview_lines = []
            for i, r in enumerate(results[:5], start=1):
                mark = " → PLAYING" if i == index else ""
                ch = f" ({r['channel']})" if r.get("channel") else ""
                preview_lines.append(f"  {i}. {r['title']}{ch}{mark}")

            return {
                "content": (
                    f"Playing YouTube result #{index} for '{query}':\n"
                    f"Title: {chosen['title']}\n"
                    f"URL: {chosen['url']}\n\n"
                    f"Top matches:\n" + "\n".join(preview_lines)
                ),
                "is_error": False,
            }

        except Exception as e:
            # Last resort: still open search page
            try:
                url = self._search_url(query if query else "youtube")
                self._open(url)
                return {
                    "content": (
                        f"youtube_player error: {e}\n"
                        f"Fallback: opened search page.\n{url}"
                    ),
                    "is_error": True,
                }
            except Exception as e2:
                return {
                    "content": f"youtube_player failed: {e} | fallback also failed: {e2}",
                    "is_error": True,
                }
