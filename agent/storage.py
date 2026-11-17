"""Crash-safe atomic persistence with optional authenticated encryption."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from agent.data_protection import (
    DataProtectionError,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted_bytes,
)


def atomic_write_bytes(path: Path | str, payload: bytes) -> None:
    """Atomically replace a file using a temporary file in the same directory."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(payload)
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


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(
    path: Path | str,
    value: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    encrypt: bool = False,
) -> None:
    destination = Path(path)
    payload = json.dumps(value, indent=indent, ensure_ascii=ensure_ascii).encode("utf-8")
    if encrypt:
        payload = encrypt_bytes(payload, context=destination.name)
    else:
        payload += b"\n"
    atomic_write_bytes(destination, payload)


def load_json(
    path: Path | str,
    default: Any = None,
    *,
    encrypt: bool = False,
) -> Any:
    """Read JSON, transparently decrypting and optionally migrating plaintext.

    Existing plaintext JSON is atomically encrypted on first read when
    encrypt=True. Authentication/key errors propagate; callers must not replace
    encrypted data with empty defaults after a decryption failure.
    """
    source = Path(path)
    if not source.is_file():
        return default
    raw = source.read_bytes()
    was_encrypted = is_encrypted_bytes(raw)
    if was_encrypted:
        raw = decrypt_bytes(raw, context=source.name)
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except Exception as error:
        raise ValueError(f"Invalid JSON in {source}: {error}") from error
    if encrypt and not was_encrypted:
        atomic_write_json(source, value, encrypt=True)
    return value


def atomic_write_encrypted_bytes(path: Path | str, payload: bytes) -> None:
    destination = Path(path)
    atomic_write_bytes(
        destination,
        encrypt_bytes(payload, context=destination.name),
    )


def load_encrypted_bytes(path: Path | str) -> bytes:
    """Read an encrypted backup; migrate legacy plaintext backups on access."""
    source = Path(path)
    raw = source.read_bytes()
    if is_encrypted_bytes(raw):
        return decrypt_bytes(raw, context=source.name)
    atomic_write_encrypted_bytes(source, raw)
    return raw
