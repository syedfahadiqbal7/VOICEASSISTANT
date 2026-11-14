"""Crash-safer local persistence helpers for JSON-backed application state."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a file using a temporary file in the same directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def atomic_write_json(
    path: Path | str,
    value: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    payload = json.dumps(value, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, payload + "\n")
