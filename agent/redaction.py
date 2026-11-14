# agent/redaction.py
"""
Redact secrets before sending prompts to a cloud LLM.
Local Ollama traffic is not redacted (stays on machine).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Tuple

# Patterns: (regex, replacement)
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # OpenAI / generic API keys
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"), "[REDACTED_GOOGLE_KEY]"),
    # Bearer tokens
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_\.=]{20,}"), r"\1[REDACTED_BEARER]"),
    # Authorization headers in text
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(secret\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(password\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(passwd\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(token\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    # Connection strings
    (re.compile(r"(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s]+"), "[REDACTED_DB_URL]"),
    (re.compile(r"(?i)(Server=.*?;(Password|Pwd)=)[^;]+"), r"\1[REDACTED]"),
    # Private keys blocks
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # Email + password combo rough
    (re.compile(r"(?i)(password\s+is\s+)\S+"), r"\1[REDACTED]"),
    # Credit-card-ish (coarse)
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED_NUMBER]"),
]


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deep-copy messages and redact string content fields."""
    redacted = copy.deepcopy(messages)
    for msg in redacted:
        if not isinstance(msg, dict):
            continue
        c = msg.get("content")
        if isinstance(c, str):
            msg["content"] = redact_text(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = redact_text(part["text"])
        # tool call arguments may contain secrets
        tcs = msg.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                    fn["arguments"] = redact_text(fn["arguments"])
    return redacted
