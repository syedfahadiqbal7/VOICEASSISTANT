"""Local-only protected data-key bootstrap and rotation operations.

These APIs are intentionally not exposed through the mobile bridge or any
network endpoint. Rotation changes the active DEK for future writes; retained
keys keep existing VAENC1/VAENC2 files readable. It does not rewrite stores.
"""
from __future__ import annotations

import copy
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.backup_manager import BackupManager
from agent.data_protection import DataProtectionError, get_data_key
from agent.key_admin_lock import KeyAdminLock, KeyAdminLockError
from agent.keyring import (
    KeyringError,
    create_initial_keyring,
    get_active_key,
    load_keyring,
    save_keyring,
    validate_keyring,
)
from agent.storage import atomic_write_json, load_json


class KeyRotationError(RuntimeError):
    """Raised when local key administration cannot safely proceed."""


DEFAULT_BACKUP_MAX_AGE_SECONDS = 24 * 60 * 60
_AUDIT_FILE = "audit_log.json"


def _history_dir(base_dir: Path | str) -> Path:
    root = Path(base_dir).resolve(strict=True)
    history = root / "history"
    if history.is_symlink():
        raise KeyRotationError("Runtime history directory must not be a symbolic link")
    history.mkdir(parents=True, exist_ok=True)
    return history


def _keyring_path(history: Path) -> Path:
    from agent import keyring as keyring_module

    path = Path(keyring_module.KEYRING_FILE)
    if not path.is_absolute():
        path = history / path
    if path.is_symlink() or path.parent.resolve(strict=True) != history.resolve(strict=True):
        raise KeyRotationError("Protected keyring must be a regular file inside the runtime history directory")
    return path


def _verify_backup(
    base_dir: Path | str,
    backup_path: Path | str,
    passphrase: str,
    *,
    max_age_seconds: int = DEFAULT_BACKUP_MAX_AGE_SECONDS,
    now: float | None = None,
) -> dict:
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("Backup maximum age must be a positive integer")
    source = Path(backup_path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise KeyRotationError("A recent regular portable backup file is required")
    info = source.stat()
    current = time.time() if now is None else now
    age = current - info.st_mtime
    if age < -300 or age > max_age_seconds:
        raise KeyRotationError("Portable backup is too old or has an invalid future timestamp")
    try:
        verified = BackupManager(base_dir).verify_backup(source, passphrase)
    except (DataProtectionError, OSError, ValueError) as error:
        raise KeyRotationError(f"Portable backup verification failed: {error}") from error
    return {
        **verified,
        "age_seconds": max(0, int(age)),
    }


def _append_audit(history: Path, event: str, **details: Any) -> None:
    path = history / _AUDIT_FILE
    if path.is_symlink():
        raise KeyRotationError("Audit log must not be a symbolic link")
    records = load_json(path, default=[], encrypt=True)
    if not isinstance(records, list):
        raise KeyRotationError("Encrypted audit log has an invalid data shape")
    records.append({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "local_key_admin_cli",
        **details,
    })
    atomic_write_json(path, records, encrypt=True)


def key_status(base_dir: Path | str) -> dict:
    history = _history_dir(base_dir)
    path = _keyring_path(history)
    if not path.is_file():
        return {
            "keyring_present": False,
            "mode": "legacy_single_key",
            "active_key_id": None,
            "retained_keys": 0,
            "legacy_key_retained": False,
        }
    try:
        ring = load_keyring(path)
        active_id, _active_key = get_active_key(ring)
    except KeyringError as error:
        raise KeyRotationError(f"Protected keyring cannot be unlocked: {error}") from error
    states = [entry["state"] for entry in ring["keys"].values()]
    return {
        "keyring_present": True,
        "mode": "keyring",
        "active_key_id": active_id,
        "retained_keys": sum(state == "retained" for state in states),
        "legacy_key_retained": any(state == "legacy" for state in states),
        "total_keys": len(states),
    }


def bootstrap_keyring(
    base_dir: Path | str,
    backup_path: Path | str,
    backup_passphrase: str,
    *,
    confirm: bool = False,
    max_backup_age_seconds: int = DEFAULT_BACKUP_MAX_AGE_SECONDS,
) -> dict:
    """Explicitly establish a keyring while preserving the effective legacy key."""
    if not confirm:
        raise PermissionError("Keyring bootstrap requires explicit local confirmation")
    history = _history_dir(base_dir)
    path = _keyring_path(history)
    try:
        with KeyAdminLock(history):
            backup = _verify_backup(
                base_dir, backup_path, backup_passphrase,
                max_age_seconds=max_backup_age_seconds,
            )
            if path.exists():
                raise KeyRotationError("A protected keyring already exists; refusing to replace it")
            _append_audit(history, "data_key.bootstrap_started", backup_sha256=backup["sha256"])
            legacy_key, source = get_data_key()
            ring = create_initial_keyring(legacy_key, path, source=source)
            active_id, _active = get_active_key(ring)
            _append_audit(
                history, "data_key.bootstrap_completed",
                active_key_id=active_id, backup_sha256=backup["sha256"],
            )
            return {
                "ok": True,
                "operation": "bootstrap",
                "active_key_id": active_id,
                "legacy_key_retained": True,
                "backup_files_verified": backup["files"],
            }
    except KeyAdminLockError as error:
        raise KeyRotationError(str(error)) from error


def rotate_data_key(
    base_dir: Path | str,
    backup_path: Path | str,
    backup_passphrase: str,
    *,
    confirm: bool = False,
    max_backup_age_seconds: int = DEFAULT_BACKUP_MAX_AGE_SECONDS,
) -> dict:
    """Activate a new random DEK and retain every old key for existing ciphertext."""
    if not confirm:
        raise PermissionError("Data-key rotation requires explicit local confirmation")
    history = _history_dir(base_dir)
    path = _keyring_path(history)
    try:
        with KeyAdminLock(history):
            backup = _verify_backup(
                base_dir, backup_path, backup_passphrase,
                max_age_seconds=max_backup_age_seconds,
            )
            if not path.is_file():
                raise KeyRotationError("No keyring exists; run the explicit bootstrap command first")
            try:
                current = load_keyring(path)
                old_active_id, _old_active_key = get_active_key(current)
            except KeyringError as error:
                raise KeyRotationError(f"Protected keyring cannot be unlocked: {error}") from error

            _append_audit(
                history, "data_key.rotation_started",
                previous_active_key_id=old_active_id,
                backup_sha256=backup["sha256"],
            )
            updated = copy.deepcopy(current)
            new_id = os.urandom(16).hex()
            while new_id in updated["keys"]:
                new_id = os.urandom(16).hex()
            updated["keys"][old_active_id]["state"] = "retained"
            updated["keys"][new_id] = {
                "key": base64.b64encode(os.urandom(32)).decode("ascii"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "state": "active",
            }
            updated["active_key_id"] = new_id
            validated = validate_keyring(updated)
            save_keyring(validated, path)

            # Reopen independently before reporting success. If this fails, do
            # not roll back blindly: another process may already have written
            # VAENC2 with the newly activated key. Both generations stay retained.
            reopened = load_keyring(path)
            active_id, _active_key = get_active_key(reopened)
            if active_id != new_id:
                raise KeyRotationError("Persisted keyring does not reference the newly activated key")
            audit_warning = None
            try:
                _append_audit(
                    history, "data_key.rotation_completed",
                    previous_active_key_id=old_active_id,
                    active_key_id=new_id,
                    backup_sha256=backup["sha256"],
                )
            except Exception as error:
                audit_warning = str(error)
            return {
                "ok": True,
                "operation": "rotate",
                "previous_active_key_id": old_active_id,
                "active_key_id": new_id,
                "retained_keys": sum(
                    entry["state"] == "retained" for entry in reopened["keys"].values()
                ),
                "legacy_key_retained": any(
                    entry["state"] == "legacy" for entry in reopened["keys"].values()
                ),
                "backup_files_verified": backup["files"],
                "audit_warning": audit_warning,
                "existing_files_rewritten": 0,
            }
    except KeyAdminLockError as error:
        raise KeyRotationError(str(error)) from error
