"""Explicit, resumable VAENC1 -> VAENC2 migration primitives.

This module never runs at application startup. Callers must supply an explicit
inventory and separately confirm that a verified backup exists before writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from agent.data_protection import (
    DataProtectionError,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted_bytes,
)
from agent.storage import atomic_write_bytes

_V1 = b"VAENC1"
_V2 = b"VAENC2"
_JOURNAL_NAME = ".voiceassistant_migration_v1.json"
_LOCK_NAME = ".voiceassistant_migration_v1.lock"
_MAX_JOURNAL_BYTES = 2 * 1024 * 1024


class MigrationError(RuntimeError):
    """Raised when migration cannot safely proceed."""


@dataclass(frozen=True)
class MigrationTarget:
    path: Path
    context: str


def discover_runtime_targets(base_dir: Path | str) -> list[MigrationTarget]:
    """Discover only documented encrypted stores; never recursively scan a tree."""
    root = Path(base_dir).resolve()
    history = root / "history"
    if history.is_symlink():
        raise MigrationError("Runtime history directory must not be a symbolic link")
    if not history.exists():
        return []
    names = {
        "conversation_history.json", "audit_log.json", "agent_memory.json",
        "agent_policy.json", "scheduled_jobs.json", "undo_stack.json",
        "skill_packs.json", "data_retention.json",
    }
    candidates = [history / name for name in sorted(names)]
    custom = history / "custom_skills"
    if custom.is_symlink():
        raise MigrationError("Custom skills directory must not be a symbolic link")
    if custom.is_dir():
        candidates.extend(sorted(custom.glob("*.json")))
    undo = history / "undo_backups"
    if undo.is_symlink():
        raise MigrationError("Undo backup directory must not be a symbolic link")
    if undo.is_dir():
        candidates.extend(
            path for path in sorted(undo.iterdir())
            if path.is_file() and not path.name.endswith(".new")
        )
    result = []
    for path in candidates:
        if path.is_symlink():
            raise MigrationError(f"Refusing symbolic-link migration target: {path}")
        if path.is_file():
            result.append(MigrationTarget(path=path, context=path.name))
    return result


def _safe_target(root: Path, target: MigrationTarget) -> Path:
    path = Path(target.path)
    if not path.is_absolute():
        path = root / path
    if not target.context or not isinstance(target.context, str):
        raise MigrationError("Every migration target requires an explicit encryption context")
    lexical = Path(os.path.abspath(str(path)))
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise MigrationError(f"Migration target escapes the configured data root: {path}") from error
    # Reject symlinks in every path component, not only the final filename.
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise MigrationError(f"Refusing symbolic-link migration path component: {current}")
    resolved = path.resolve(strict=True)
    try:
        if os.path.commonpath([os.path.normcase(str(root)), os.path.normcase(str(resolved))]) != os.path.normcase(str(root)):
            raise MigrationError(f"Migration target escapes the configured data root: {path}")
    except ValueError as error:
        raise MigrationError(f"Migration target is on an unexpected volume: {path}") from error
    if not resolved.is_file():
        raise MigrationError(f"Migration target is not a regular file: {path}")
    return resolved


class _MigrationLock:
    """Exclusive-create lock; stale locks fail closed and require operator review."""
    def __init__(self, path: Path):
        self.path = path
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "pid": os.getpid(), "created_at": time.time(),
        }).encode("utf-8")
        try:
            self.descriptor = os.open(
                str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.write(self.descriptor, payload)
            os.fsync(self.descriptor)
        except FileExistsError as error:
            raise MigrationError(
                f"Another migration lock exists at {self.path}; verify its owner before removing it"
            ) from error
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _root_binding(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Some filesystems do not support directory fsync.
        pass


def _write_journal(path: Path, value: dict) -> None:
    encoded = json.dumps(value, sort_keys=True, indent=2).encode("utf-8")
    if len(encoded) > _MAX_JOURNAL_BYTES:
        raise MigrationError("Migration journal exceeds its safety limit")
    atomic_write_bytes(path, encoded + b"\n")


def _load_journal(path: Path, root: Path) -> dict:
    if path.is_symlink():
        raise MigrationError("Migration journal must not be a symbolic link")
    if not path.exists():
        return {"schema_version": 1, "root_id": _root_binding(root), "files": {}}
    raw = path.read_bytes()
    if len(raw) > _MAX_JOURNAL_BYTES:
        raise MigrationError("Migration journal exceeds its safety limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as error:
        raise MigrationError("Migration journal is malformed; refusing to overwrite it") from error
    if (
        not isinstance(value, dict) or value.get("schema_version") != 1
        or value.get("root_id") != _root_binding(root) or not isinstance(value.get("files"), dict)
    ):
        raise MigrationError("Migration journal schema or data-root binding is invalid")
    return value


def _fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inspect_migration(
    base_dir: Path | str,
    targets: Iterable[MigrationTarget] | None = None,
) -> dict:
    """Read-only inventory. Plaintext and unknown files are reported, never rewritten."""
    root = Path(base_dir).resolve(strict=True)
    selected = list(targets if targets is not None else discover_runtime_targets(root))
    rows = []
    seen_paths = set()
    for item in selected:
        path = _safe_target(root, item)
        identity = os.path.normcase(str(path))
        if identity in seen_paths:
            raise MigrationError(f"Duplicate migration target: {path}")
        seen_paths.add(identity)
        raw = path.read_bytes()
        if raw.startswith(_V1):
            state = "legacy_vaenc1"
        elif raw.startswith(_V2):
            state = "current_vaenc2"
        elif is_encrypted_bytes(raw):
            state = "unsupported_envelope"
        else:
            state = "plaintext_or_unknown"
        rows.append({
            "path": str(path.relative_to(root)).replace(os.sep, "/"),
            "context": item.context,
            "state": state,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return {
        "root": str(root),
        "total": len(rows),
        "legacy_vaenc1": sum(row["state"] == "legacy_vaenc1" for row in rows),
        "current_vaenc2": sum(row["state"] == "current_vaenc2" for row in rows),
        "plaintext_or_unknown": sum(row["state"] == "plaintext_or_unknown" for row in rows),
        "unsupported_envelope": sum(row["state"] == "unsupported_envelope" for row in rows),
        "files": rows,
    }


def migrate_to_vaenc2(
    base_dir: Path | str,
    targets: Iterable[MigrationTarget] | None = None,
    *,
    confirm: bool = False,
    backup_verified: bool = False,
) -> dict:
    """Migrate explicit VAENC1 targets with per-file verification and restartability."""
    if not confirm or not backup_verified:
        raise PermissionError(
            "Migration requires explicit confirm=True and backup_verified=True"
        )
    root = Path(base_dir).resolve(strict=True)
    selected = list(targets if targets is not None else discover_runtime_targets(root))
    identities = set()
    for item in selected:
        normalized = os.path.normcase(str(_safe_target(root, item)))
        if normalized in identities:
            raise MigrationError(f"Duplicate migration target: {normalized}")
        identities.add(normalized)
    history = root / "history"
    if history.is_symlink():
        raise MigrationError("Runtime history directory must not be a symbolic link")
    history.mkdir(parents=True, exist_ok=True)
    journal_path = history / _JOURNAL_NAME
    lock_path = history / _LOCK_NAME
    if journal_path.is_symlink() or lock_path.is_symlink():
        raise MigrationError("Migration journal and lock must not be symbolic links")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    result = {"migrated": 0, "already_current": 0, "failed": [], "files": []}

    with _MigrationLock(lock_path):
        journal = _load_journal(journal_path, root)
        for item in selected:
            try:
                path = _safe_target(root, item)
                relative = str(path.relative_to(root)).replace(os.sep, "/")
                original_stat = _fingerprint(path)
                original = path.read_bytes()
                digest_before = hashlib.sha256(original).hexdigest()
                if original.startswith(_V2):
                    clear = decrypt_bytes(original, item.context)
                    journal["files"][relative] = {
                        "status": "verified_current",
                        "ciphertext_sha256": digest_before,
                        "plaintext_sha256": hashlib.sha256(clear).hexdigest(),
                    }
                    result["already_current"] += 1
                    result["files"].append({"path": relative, "status": "already_current"})
                    _write_journal(journal_path, journal)
                    continue
                if not original.startswith(_V1):
                    raise MigrationError(
                        "Only VAENC1 files are eligible; plaintext/unknown formats are never rewritten"
                    )
                clear = decrypt_bytes(original, item.context)
                clear_digest = hashlib.sha256(clear).hexdigest()
                replacement = encrypt_bytes(clear, item.context)
                if not replacement.startswith(_V2):
                    raise MigrationError("Active keyring did not produce a VAENC2 envelope")

                temporary = None
                try:
                    descriptor, temporary = tempfile.mkstemp(
                        prefix=f".{path.name}.migration.", suffix=".tmp", dir=str(path.parent)
                    )
                    try:
                        if os.name != "nt":
                            os.fchmod(descriptor, stat.S_IMODE(path.stat().st_mode))
                        with os.fdopen(descriptor, "wb") as handle:
                            handle.write(replacement)
                            handle.flush()
                            os.fsync(handle.fileno())
                    except Exception:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                        raise

                    staged = Path(temporary).read_bytes()
                    staged_clear = decrypt_bytes(staged, item.context)
                    if hashlib.sha256(staged_clear).hexdigest() != clear_digest:
                        raise MigrationError("Staged VAENC2 plaintext digest verification failed")
                    if _fingerprint(path) != original_stat:
                        raise MigrationError("Source changed during migration; refusing atomic replacement")
                    os.replace(temporary, path)
                    temporary = None
                    _fsync_directory(path.parent)
                    try:
                        persisted = path.read_bytes()
                        persisted_clear = decrypt_bytes(persisted, item.context)
                        if (
                            not persisted.startswith(_V2)
                            or hashlib.sha256(persisted_clear).hexdigest() != clear_digest
                        ):
                            raise MigrationError("Persisted VAENC2 digest verification failed")
                    except Exception:
                        atomic_write_bytes(path, original)
                        _fsync_directory(path.parent)
                        raise
                finally:
                    if temporary:
                        try:
                            os.unlink(temporary)
                        except OSError:
                            pass

                new_digest = hashlib.sha256(path.read_bytes()).hexdigest()
                journal["files"][relative] = {
                    "status": "migrated_verified",
                    "old_ciphertext_sha256": digest_before,
                    "new_ciphertext_sha256": new_digest,
                    "plaintext_sha256": clear_digest,
                }
                _write_journal(journal_path, journal)
                result["migrated"] += 1
                result["files"].append({"path": relative, "status": "migrated_verified"})
            except Exception as error:
                result["failed"].append({"path": str(item.path), "error": str(error)})
                result["files"].append({"path": str(item.path), "status": "failed"})
                # Persist prior completed work so a later invocation can resume.
                _write_journal(journal_path, journal)
                break

        result["journal"] = str(journal_path)
        result["complete"] = not result["failed"] and all(
            row["status"] in {"migrated_verified", "already_current"}
            for row in result["files"]
        )
    return result
