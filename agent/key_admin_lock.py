"""Cross-process serialization for local data-key administration."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class KeyAdminLockError(RuntimeError):
    """Raised when another key-administration operation owns the lock."""


class KeyAdminLock:
    """Exclusive-create lock shared by rotation and encrypted-store migration.

    Stale locks deliberately fail closed. An operator must inspect the recorded
    PID and process state before manually removing one.
    """

    NAME = ".voiceassistant_key_admin.lock"

    def __init__(self, history_dir: Path | str):
        self.path = Path(history_dir) / self.NAME
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "pid": os.getpid(),
            "created_at": time.time(),
            "operation": "key-administration",
        }, sort_keys=True).encode("utf-8")
        try:
            self.descriptor = os.open(
                str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError as error:
            raise KeyAdminLockError(
                "Another local key-administration operation holds the lock; "
                "inspect its owner before removing a stale lock"
            ) from error
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(self.descriptor, remaining)
                if written <= 0:
                    raise OSError("Could not persist key-administration lock metadata")
                remaining = remaining[written:]
            os.fsync(self.descriptor)
        except Exception:
            os.close(self.descriptor)
            self.descriptor = None
            try:
                self.path.unlink()
            except OSError:
                pass
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
