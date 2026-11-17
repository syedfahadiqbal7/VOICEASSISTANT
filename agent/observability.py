"""Structured, secret-redacting application logging."""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.redaction import redact_text


class RedactingJsonFormatter(logging.Formatter):
    """Emit one JSON object per line, redacting messages and exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.name),
            "message": redact_text(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    log_dir: Optional[Path] = None,
    *,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure a single rotating UTF-8 JSONL sink; safe to call repeatedly."""
    logger = logging.getLogger("voiceassistant")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if any(getattr(handler, "_voiceassistant_sink", False) for handler in logger.handlers):
        return logger

    destination = Path(log_dir) if log_dir is not None else (
        Path(__file__).resolve().parents[1] / "history" / "logs"
    )
    try:
        destination.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            destination / "voiceassistant.jsonl",
            maxBytes=max(1024, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
            delay=True,
        )
    except OSError:
        handler = logging.StreamHandler()

    handler._voiceassistant_sink = True
    handler.setFormatter(RedactingJsonFormatter())
    logger.addHandler(handler)
    return logger
