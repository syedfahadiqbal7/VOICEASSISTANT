"""Portable, passphrase-protected backups of VoiceAssistant private runtime data.

Backups contain decrypted application data only inside an AES-GCM encrypted
archive. The archive is portable across machines because it is protected by a
user-supplied passphrase, then restored into the destination machine's local
data-encryption key.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from agent.data_protection import DataProtectionError
from agent.storage import (
    atomic_write_bytes,
    atomic_write_encrypted_bytes,
    atomic_write_json,
    load_encrypted_bytes,
    load_json,
)

_MAGIC = b"VABACKUP"
_VERSION = 1
_SALT_SIZE = 16
_NONCE_SIZE = 12
_KEY_SIZE = 32
_PBKDF2_ITERATIONS = 600_000
_AAD = b"VoiceAssistant.PortableBackup.v1"
_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
_MAX_BACKUP_BYTES = _MAX_ARCHIVE_BYTES + 1024
_MAX_ENTRIES = 500
_JSON_STORE_NAMES = {
    "conversation_history.json",
    "audit_log.json",
    "agent_memory.json",
    "agent_policy.json",
    "scheduled_jobs.json",
    "undo_stack.json",
    "skill_packs.json",
    "data_retention.json",
}


def _validate_passphrase(passphrase: str) -> None:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("Backup passphrase must contain at least 12 characters")


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    _validate_passphrase(passphrase)
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    ).derive(passphrase.encode("utf-8"))


def _encrypt_archive(archive: bytes, passphrase: str) -> bytes:
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    key = _derive_key(passphrase, salt)
    encrypted = AESGCM(key).encrypt(nonce, archive, _AAD)
    return _MAGIC + bytes([_VERSION]) + salt + nonce + encrypted


def _decrypt_archive(payload: bytes, passphrase: str) -> bytes:
    header_size = len(_MAGIC) + 1 + _SALT_SIZE + _NONCE_SIZE
    if not payload.startswith(_MAGIC) or len(payload) <= header_size:
        raise DataProtectionError("Not a valid VoiceAssistant backup or backup is truncated")
    version = payload[len(_MAGIC)]
    if version != _VERSION:
        raise DataProtectionError(f"Unsupported backup version: {version}")
    salt_start = len(_MAGIC) + 1
    salt = payload[salt_start:salt_start + _SALT_SIZE]
    nonce_start = salt_start + _SALT_SIZE
    nonce = payload[nonce_start:nonce_start + _NONCE_SIZE]
    ciphertext = payload[header_size:]
    try:
        return AESGCM(_derive_key(passphrase, salt)).decrypt(nonce, ciphertext, _AAD)
    except InvalidTag as error:
        raise DataProtectionError("Backup authentication failed (wrong passphrase or tampering)") from error


def _allowed_member(name: str) -> bool:
    if not name or "\\" in name:
        return False
    raw_parts = name.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        return False
    path = PurePosixPath(name)
    if path.is_absolute():
        return False
    if len(path.parts) == 1:
        return path.name in _JSON_STORE_NAMES
    if len(path.parts) == 2 and path.parts[0] == "custom_skills":
        return path.name == "index.json" or bool(re.fullmatch(r"[a-z][a-z0-9_]{1,39}\.json", path.name))
    if len(path.parts) == 2 and path.parts[0] == "undo_backups":
        name = path.name
        stem = name[:-4] if name.endswith(".new") else name
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        return (
            1 <= len(name) <= 240
            and not name.endswith((".", " "))
            and ":" not in name
            and not any(char in name for char in '<>"|?*')
            and stem.split(".", 1)[0].upper() not in reserved
        )
    return False


class BackupManager:
    """Create and restore portable encrypted backups of private runtime stores."""

    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir).resolve()
        self.history_dir = self.base_dir / "history"

    def _collect_entries(self) -> Dict[str, bytes]:
        entries: Dict[str, bytes] = {}
        if self.history_dir.is_symlink():
            raise DataProtectionError("Runtime history directory must not be a symbolic link")
        self.history_dir.mkdir(parents=True, exist_ok=True)

        for name in sorted(_JSON_STORE_NAMES):
            path = self.history_dir / name
            if path.is_symlink():
                raise DataProtectionError(f"Refusing to back up a symbolic link: {path}")
            if path.is_file():
                value = load_json(path, encrypt=True)
                entries[name] = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        custom_dir = self.history_dir / "custom_skills"
        if custom_dir.is_symlink():
            raise DataProtectionError(f"Refusing to back up a symbolic link directory: {custom_dir}")
        if custom_dir.is_dir():
            for path in sorted(custom_dir.glob("*.json")):
                if path.is_file() and not path.is_symlink():
                    value = load_json(path, encrypt=True)
                    relative = f"custom_skills/{path.name}"
                    entries[relative] = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        undo_dir = self.history_dir / "undo_backups"
        if undo_dir.is_symlink():
            raise DataProtectionError(f"Refusing to back up a symbolic link directory: {undo_dir}")
        if undo_dir.is_dir():
            for path in sorted(undo_dir.iterdir()):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = f"undo_backups/{path.name}"
                if path.name.endswith(".new"):
                    entries[relative] = path.read_bytes()
                else:
                    entries[relative] = load_encrypted_bytes(path)

        if len(entries) > _MAX_ENTRIES:
            raise ValueError(f"Backup contains too many files (maximum {_MAX_ENTRIES})")
        if sum(len(value) for value in entries.values()) > _MAX_ARCHIVE_BYTES:
            raise ValueError("Backup data exceeds the 50 MiB safety limit")
        return entries

    def create_backup(
        self,
        destination: Path | str,
        passphrase: str,
        *,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        _validate_passphrase(passphrase)
        destination = Path(destination)
        if destination.exists() and not overwrite:
            raise FileExistsError(f"Backup already exists: {destination}")
        entries = self._collect_entries()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                if not _allowed_member(name):
                    raise ValueError(f"Refusing unsafe backup member: {name}")
                archive.writestr(name, data)
        archive_bytes = buffer.getvalue()
        if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
            raise ValueError("Compressed backup exceeds the 50 MiB safety limit")
        encrypted = _encrypt_archive(archive_bytes, passphrase)
        if len(encrypted) > _MAX_BACKUP_BYTES:
            raise ValueError("Encrypted backup exceeds the configured size limit")
        atomic_write_bytes(destination, encrypted)
        return {
            "ok": True,
            "path": str(destination),
            "files": len(entries),
            "bytes": len(encrypted),
            "encrypted": True,
        }

    def _read_archive(self, payload: bytes, passphrase: str) -> List[Tuple[str, bytes]]:
        if len(payload) > _MAX_BACKUP_BYTES:
            raise DataProtectionError("Backup exceeds the configured size limit")
        archive_bytes = _decrypt_archive(payload, passphrase)
        if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
            raise DataProtectionError("Decrypted backup exceeds the configured size limit")
        result: List[Tuple[str, bytes]] = []
        seen = set()
        total = 0
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
                infos = archive.infolist()
                if len(infos) > _MAX_ENTRIES:
                    raise DataProtectionError("Backup contains too many files")
                for info in infos:
                    name = info.filename
                    if info.is_dir() or not _allowed_member(name) or name in seen:
                        raise DataProtectionError(f"Backup contains an unsafe or duplicate path: {name}")
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if mode and (mode & 0o170000) == 0o120000:
                        raise DataProtectionError(f"Backup symlink is not allowed: {name}")
                    if info.flag_bits & 0x1:
                        raise DataProtectionError("Encrypted ZIP members are not supported")
                    total += info.file_size
                    if total > _MAX_ARCHIVE_BYTES:
                        raise DataProtectionError("Backup expands beyond the 50 MiB safety limit")
                    if info.file_size > 0 and info.compress_size == 0:
                        raise DataProtectionError("Backup contains an invalid compressed member")
                    if info.compress_size and info.file_size / info.compress_size > 200:
                        raise DataProtectionError("Backup compression ratio exceeds safety limit")
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        raise DataProtectionError(f"Backup member size mismatch: {name}")
                    if name.endswith(".json") and name.split("/", 1)[0] != "undo_backups":
                        try:
                            json.loads(data.decode("utf-8-sig"))
                        except Exception as error:
                            raise DataProtectionError(f"Invalid JSON backup member: {name}") from error
                    seen.add(name)
                    result.append((name, data))
        except DataProtectionError:
            raise
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
            raise DataProtectionError(f"Backup archive is invalid: {error}") from error
        return result

    def verify_backup(self, source: Path | str, passphrase: str) -> Dict[str, Any]:
        """Authenticate and fully validate a portable backup without restoring it."""
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise DataProtectionError("Backup source must be an existing regular file")
        before = source.stat()
        if before.st_size > _MAX_BACKUP_BYTES:
            raise DataProtectionError("Backup exceeds the configured size limit")
        payload = source.read_bytes()
        entries = self._read_archive(payload, passphrase)
        after = source.stat()
        if (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise DataProtectionError("Backup changed while it was being verified")
        return {
            "ok": True,
            "encrypted": True,
            "files": len(entries),
            "bytes": after.st_size,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "file_names": sorted(name for name, _data in entries),
        }

    def restore_backup(
        self,
        source: Path | str,
        passphrase: str,
        *,
        confirm_overwrite: bool = False,
    ) -> Dict[str, Any]:
        if not confirm_overwrite:
            raise PermissionError("Restore can overwrite local data; explicit confirm_overwrite=True is required")
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise DataProtectionError("Backup source must be an existing regular file")
        if source.stat().st_size > _MAX_BACKUP_BYTES:
            raise DataProtectionError("Backup exceeds the configured size limit")
        entries = self._read_archive(source.read_bytes(), passphrase)

        if self.history_dir.is_symlink():
            raise DataProtectionError("Runtime history directory must not be a symbolic link")
        targets = [(name, self.history_dir.joinpath(*PurePosixPath(name).parts), data) for name, data in entries]
        root = self.history_dir.resolve()
        for _name, target, _data in targets:
            if target.is_symlink():
                raise DataProtectionError(f"Refusing to restore through a symbolic link: {target}")
            parent = target.parent
            if parent.is_symlink():
                raise DataProtectionError(f"Refusing to restore through a symbolic link directory: {parent}")
            try:
                if os.path.commonpath([os.path.normcase(str(root)), os.path.normcase(str(parent.resolve(strict=False)))]) != os.path.normcase(str(root)):
                    raise DataProtectionError(f"Restore path escapes the runtime data directory: {target}")
            except ValueError as error:
                raise DataProtectionError(f"Restore path is on an unexpected volume: {target}") from error

        parsed_json: Dict[str, Any] = {}
        for name, _target, data in targets:
            if name.endswith(".json") and not name.startswith("undo_backups/"):
                value = json.loads(data.decode("utf-8-sig"))
                parsed_json[name] = value
                expected = {
                    "conversation_history.json": dict,
                    "audit_log.json": list,
                    "agent_memory.json": dict,
                    "agent_policy.json": dict,
                    "scheduled_jobs.json": list,
                    "undo_stack.json": list,
                    "skill_packs.json": dict,
                    "data_retention.json": dict,
                }.get(name)
                if expected and not isinstance(value, expected):
                    raise DataProtectionError(f"Unexpected data shape for backup store: {name}")
                if name.startswith("custom_skills/") and name != "custom_skills/index.json":
                    if not isinstance(value, dict) or not value.get("skill_id"):
                        raise DataProtectionError(f"Invalid custom skill manifest: {name}")
                    from agent.skill_author import validate_skill
                    valid, errors = validate_skill(value)
                    if not valid:
                        raise DataProtectionError(f"Invalid custom skill manifest {name}: {'; '.join(errors)}")

        # Validate every member before changing any destination. Each file is
        # then atomically replaced; this is not a filesystem-wide transaction.
        restored = []
        custom_manifests: List[Dict[str, Any]] = []
        for name, target, data in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".json") and not name.startswith("undo_backups/"):
                value = parsed_json[name]
                atomic_write_json(target, value, ensure_ascii=False, encrypt=True)
                if name.startswith("custom_skills/") and target.name != "index.json":
                    custom_manifests.append(value)
            elif name.startswith("undo_backups/") and name.endswith(".new"):
                atomic_write_bytes(target, data)
            else:
                atomic_write_encrypted_bytes(target, data)
            restored.append(name)

        # Generated skill plugins contain no private action payloads; rebuild
        # them from the authenticated, validated encrypted manifests.
        if custom_manifests:
            from agent.skill_author import generate_plugin_source, validate_skill
            plugins_dir = self.base_dir / "plugins"
            plugins_dir.mkdir(parents=True, exist_ok=True)
            for manifest in custom_manifests:
                valid, errors = validate_skill(manifest)
                if not valid:
                    raise DataProtectionError(
                        f"Cannot regenerate custom skill {manifest.get('skill_id')}: {'; '.join(errors)}"
                    )
                skill_id = manifest["skill_id"]
                plugin_path = plugins_dir / f"{skill_id}_skill.py"
                atomic_write_bytes(plugin_path, generate_plugin_source(manifest).encode("utf-8"))

        return {"ok": True, "restored_files": restored, "count": len(restored)}
