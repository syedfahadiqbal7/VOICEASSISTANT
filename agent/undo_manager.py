# agent/undo_manager.py
"""
Phase 2 — Undo / rollback for reversible actions (especially file writes).
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import BASE_DIR
from agent.storage import atomic_write_json, atomic_write_bytes, load_json, atomic_write_encrypted_bytes, load_encrypted_bytes
from agent.data_protection import DataProtectionError

UNDO_DIR = BASE_DIR / "history" / "undo_backups"
UNDO_LOG = BASE_DIR / "history" / "undo_stack.json"


class UndoManager:
    def __init__(self, max_entries: int = 30):
        self.max_entries = max_entries
        UNDO_DIR.mkdir(parents=True, exist_ok=True)
        self.stack: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not UNDO_LOG.exists():
            return
        data = load_json(UNDO_LOG, default=[], encrypt=True)
        if not isinstance(data, list):
            raise ValueError("undo stack must be a JSON array")
        self.stack = data[-self.max_entries :]

    def _save(self) -> None:
        atomic_write_json(UNDO_LOG, self.stack[-self.max_entries :], indent=2, ensure_ascii=False, encrypt=True)

    def _clean_path(self, filepath: str) -> str:
        filepath = (filepath or "").replace("\\", "/").strip().strip('"').strip("'")
        home = str(Path.home()).replace("\\", "/")
        filepath = filepath.replace("...", "")
        for placeholder in ("YourUsername", "YourName", "yourusername", "username", "YourUserName"):
            if placeholder in filepath:
                filepath = filepath.replace(f"C:/Users/{placeholder}", home).replace(
                    placeholder, os.path.basename(home)
                )
        low = filepath.lower()
        if low == "desktop":
            filepath = f"{home}/Desktop"
        elif low.startswith("desktop/"):
            filepath = f"{home}/Desktop/{filepath.split('/', 1)[1]}"
        return os.path.expandvars(os.path.expanduser(filepath))

    def snapshot_before_write(self, filepath: str) -> Optional[str]:
        """Copy existing file to undo backup; return backup path or None if new file."""
        filepath = self._clean_path(filepath)
        if not filepath:
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe = filepath.replace(":", "").replace("/", "_")
        backup = UNDO_DIR / f"{ts}__{safe}"
        if os.path.isfile(filepath):
            try:
                atomic_write_encrypted_bytes(backup, Path(filepath).read_bytes())
                return str(backup)
            except Exception as e:
                raise OSError(f"Could not create undo snapshot for {filepath}: {e}") from e
        # New-file marker: if this cannot be written, do not allow the write to proceed.
        marker = str(backup) + ".new"
        Path(marker).write_text("NEW_FILE", encoding="utf-8")
        return marker

    def record_write(self, filepath: str, backup_path: Optional[str], content_preview: str = "") -> None:
        filepath = self._clean_path(filepath)
        entry = {
            "type": "file_write",
            "filepath": filepath,
            "backup_path": backup_path,
            "content_preview": (content_preview or "")[:200],
            "timestamp": datetime.now().isoformat(),
        }
        # Avoid consecutive duplicate entries for same file
        if self.stack and self.stack[-1].get("filepath") == filepath and self.stack[-1].get("type") == "file_write":
            self.stack[-1] = entry
        else:
            self.stack.append(entry)
        self.stack = self.stack[-self.max_entries :]
        self._save()

    def rollback_unrecorded_write(self, filepath: str, backup_path: str) -> Dict[str, Any]:
        """Restore a write when its undo-stack entry could not be persisted.

        The snapshot must be one created by this UndoManager. Existing files are
        restored from their encrypted snapshot; newly-created files are removed
        only when the matching NEW_FILE marker is present.
        """
        filepath = self._clean_path(filepath)
        if not filepath or not backup_path:
            raise ValueError("A target path and undo snapshot are required")

        backup = Path(backup_path)
        undo_root = UNDO_DIR.resolve()
        if backup.is_symlink():
            raise OSError("Undo snapshot must not be a symbolic link")
        lexical_backup = Path(os.path.abspath(str(backup)))
        try:
            lexical_backup.relative_to(undo_root)
        except ValueError as error:
            raise OSError("Undo snapshot is outside the configured undo directory") from error
        if not backup.is_file():
            raise FileNotFoundError(f"Undo snapshot is missing: {backup}")

        target = Path(filepath)
        if target.is_symlink():
            raise OSError("Refusing to roll back through a symbolic-link target")

        if backup.name.endswith(".new"):
            if backup.read_text(encoding="utf-8") != "NEW_FILE":
                raise OSError("New-file marker is invalid; refusing to delete the target")
            if target.exists():
                if not target.is_file():
                    raise OSError("New-file rollback target is not a regular file")
                target.unlink()
        else:
            restored = load_encrypted_bytes(backup)
            atomic_write_bytes(target, restored)
            if not target.is_file() or target.read_bytes() != restored:
                raise OSError("Restored file did not pass byte-for-byte verification")

        backup.unlink(missing_ok=True)
        self.stack = [
            entry for entry in self.stack
            if not (
                entry.get("type") == "file_write"
                and self._clean_path(entry.get("filepath") or "") == filepath
                and entry.get("backup_path") == str(backup)
            )
        ]
        try:
            self._save()
        except Exception:
            # The file itself is already restored. A stale persisted undo entry
            # can only repeat restoration of the same prior state.
            pass
        return {"ok": True, "message": f"Restored {filepath} to its pre-write state."}

    def undo_last(self) -> Dict[str, Any]:
        if not self.stack:
            return {"ok": False, "message": "Nothing to undo."}
        entry = self.stack.pop()
        self._save()
        if entry.get("type") != "file_write":
            return {"ok": False, "message": f"Unsupported undo type: {entry.get('type')}"}

        filepath = self._clean_path(entry.get("filepath") or "")
        backup = entry.get("backup_path") or ""
        try:
            is_new = bool(
                backup.endswith(".new")
                or (backup and Path(backup).name.endswith(".new"))
            )
            if is_new:
                # Newly created file → remove if still present
                deleted = False
                if filepath and os.path.isfile(filepath):
                    os.remove(filepath)
                    deleted = True
                if backup and os.path.isfile(backup):
                    try:
                        os.remove(backup)
                    except Exception:
                        pass
                if deleted:
                    return {
                        "ok": True,
                        "message": f"Undo: deleted newly created file {filepath}",
                    }
                # Stack entry consumed, but file was already gone (manual delete / prior undo)
                return {
                    "ok": True,
                    "message": (
                        f"Undo: entry cleared — file already missing "
                        f"(not deleted again): {filepath}"
                    ),
                }

            if backup and os.path.isfile(backup):
                parent = os.path.dirname(filepath) or "."
                os.makedirs(parent, exist_ok=True)
                restored = load_encrypted_bytes(backup)
                from agent.storage import atomic_write_bytes
                atomic_write_bytes(filepath, restored)
                return {
                    "ok": True,
                    "message": f"Undo: restored {filepath} from backup",
                }
            if filepath and os.path.isfile(filepath) and not backup:
                return {
                    "ok": False,
                    "message": f"Undo: no backup for {filepath}; left file as-is",
                }
            return {
                "ok": False,
                "message": f"Undo: backup missing and file not restored: {filepath}",
            }
        except Exception as e:
            return {"ok": False, "message": f"Undo failed: {e}"}

    def list_stack(self) -> List[Dict[str, Any]]:
        return list(reversed(self.stack[-20:]))
