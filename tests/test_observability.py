import json
import logging
import sys
from pathlib import Path

from agent.observability import RedactingJsonFormatter, configure_logging


def test_json_formatter_redacts_secrets_from_messages():
    logger = logging.getLogger("voiceassistant.test.redaction")
    record = logger.makeRecord(
        logger.name, logging.ERROR, __file__, 12,
        "request failed Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
        (), None,
    )
    rendered = RedactingJsonFormatter().format(record)
    payload = json.loads(rendered)
    assert payload["level"] == "ERROR"
    assert "abcdefghijklmnopqrstuvwxyz123456" not in rendered
    assert "[REDACTED_BEARER]" in rendered


def test_json_formatter_redacts_exception_text():
    logger = logging.getLogger("voiceassistant.test.exception")
    try:
        raise RuntimeError("password=SuperSecret123")
    except RuntimeError:
        record = logger.makeRecord(
            logger.name, logging.ERROR, __file__, 20, "operation failed", (), sys.exc_info()
        )
    payload = json.loads(RedactingJsonFormatter().format(record))
    assert "SuperSecret123" not in payload["exception"]
    assert "[REDACTED]" in payload["exception"]


def test_configure_logging_is_idempotent_and_rotates(tmp_path: Path):
    logger = configure_logging(tmp_path, max_bytes=1024, backup_count=2)
    handlers_before = list(logger.handlers)
    assert configure_logging(tmp_path, max_bytes=1024, backup_count=2) is logger
    assert logger.handlers == handlers_before

    for index in range(80):
        logger.info("diagnostic event %s %s", index, "x" * 80, extra={"event": "test.rotation"})
    for handler in logger.handlers:
        handler.flush()

    assert (tmp_path / "voiceassistant.jsonl").exists()
    assert (tmp_path / "voiceassistant.jsonl.1").exists()
    content = (tmp_path / "voiceassistant.jsonl").read_text(encoding="utf-8")
    assert all(json.loads(line)["event"] == "test.rotation" for line in content.splitlines())
