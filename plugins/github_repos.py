# plugins/github_repos.py
"""List public GitHub repos for a user (no token required for public data)."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, List

from tools.base import BaseTool

SA_SIGNALS = [
    "architect", "architecture", "api", "microservice", "distributed",
    "azure", "aws", "cloud", "dotnet", ".net", "csharp", "c#",
    "payment", "banking", "hsm", "security", "integration",
    "rag", "llm", "agent", "devops", "docker", "kubernetes",
    "sql", "react", "flutter", "enterprise",
]


def _parse_user(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)", t, re.I)
    if m:
        return m.group(1)
    m = re.search(r"^@?([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)$", t)
    if m:
        return m.group(1)
    return ""


class GitHubListReposTool(BaseTool):
    @property
    def name(self) -> str:
        return "github_list_repos"

    @property
    def description(self) -> str:
        return (
            "List public repositories for a GitHub username or profile URL "
            "(e.g. https://github.com/syedfahadiqbal7/). "
            "Returns repo names, descriptions, languages, and Solution-Architect-relevant signals. "
            "Does not require a GitHub token for public repos."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "username_or_url": {
                    "type": "string",
                    "description": "GitHub username or profile URL",
                },
                "max_repos": {
                    "type": "integer",
                    "description": "Max repos to return (default 30, max 50)",
                },
            },
            "required": ["username_or_url"],
        }

    def execute(
        self, username_or_url: str = "", max_repos: int = 30, **kwargs
    ) -> Dict[str, Any]:
        user = _parse_user(username_or_url)
        if not user:
            return {
                "content": "Could not parse GitHub username. Example: https://github.com/syedfahadiqbal7/",
                "is_error": True,
            }
        try:
            max_repos = max(1, min(int(max_repos or 30), 50))
        except Exception:
            max_repos = 30

        url = (
            f"https://api.github.com/users/{user}/repos"
            f"?per_page={max_repos}&sort=updated&type=owner"
        )
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "VoiceAssistant-ForJob/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            return {
                "content": f"GitHub API failed for '{user}': {e}",
                "is_error": True,
            }

        if not isinstance(data, list):
            msg = data.get("message") if isinstance(data, dict) else str(data)
            return {"content": f"Unexpected GitHub response: {msg}", "is_error": True}

        if not data:
            return {
                "content": f"No public repos found for https://github.com/{user}/",
                "is_error": False,
            }

        lines: List[str] = [
            f"GitHub public repos for https://github.com/{user}/ ({len(data)} shown)",
            "",
        ]
        for i, repo in enumerate(data, 1):
            name = repo.get("name") or "?"
            desc = (repo.get("description") or "").strip() or "(no description)"
            lang = repo.get("language") or "n/a"
            stars = repo.get("stargazers_count", 0)
            topics = repo.get("topics") or []
            html = repo.get("html_url") or f"https://github.com/{user}/{name}"
            blob = " ".join(
                [
                    name,
                    desc,
                    lang or "",
                    " ".join(topics),
                    repo.get("homepage") or "",
                ]
            ).lower()
            hits = [s for s in SA_SIGNALS if s in blob]
            lines.append(f"{i}. {name}  ({lang}, ★{stars})")
            lines.append(f"   {desc}")
            lines.append(f"   {html}")
            if topics:
                lines.append(f"   topics: {', '.join(topics[:12])}")
            if hits:
                lines.append(f"   SA signals: {', '.join(hits[:10])}")
            else:
                lines.append("   SA signals: (none obvious from name/description)")
            lines.append("")

        lines.append("Notes:")
        lines.append(
            "- Based on public repo metadata only (not full source review)."
        )
        lines.append(
            "- Use these as evidence pointers for CV bullets; do not invent capabilities."
        )
        lines.append(
            "- Follow-up: ask the assistant to map specific repos to Solution Architect bullets."
        )
        return {"content": "\n".join(lines), "is_error": False}
