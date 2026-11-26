"""Explicitly provisioned desktop signing identity for future remote sessions.

The Ed25519 private key is independent of the data-encryption key and is stored
only inside the authenticated-encrypted local identity record. Normal startup
never creates or replaces this identity.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agent.data_protection import DataProtectionError, is_encrypted_bytes
from agent.storage import atomic_write_json
from config import HISTORY_DIR

IDENTITY_FILE = Path(HISTORY_DIR) / "desktop_remote_identity.json"
_PROCESS_LOCK = threading.RLock()
_SCHEMA_VERSION = 1
_MAX_MESSAGE_BYTES = 1024 * 1024
_MAX_IDENTITY_FILE_BYTES = 16 * 1024


class RemoteIdentityError(RuntimeError):
    """Remote identity is missing, invalid, unavailable or unsafe."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise RemoteIdentityError(f"Remote identity {field} is malformed")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as error:
        raise RemoteIdentityError(f"Remote identity {field} is malformed") from error
    if _b64encode(decoded) != value:
        raise RemoteIdentityError(f"Remote identity {field} is not canonical base64url")
    return decoded


def _contains_symlink_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(str(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if current.is_symlink():
            return True
    return False


@contextmanager
def _identity_file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    if _contains_symlink_component(path) or _contains_symlink_component(lock_path):
        raise DataProtectionError("Remote identity and lock paths must not contain symbolic links")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if _contains_symlink_component(path) or _contains_symlink_component(lock_path):
        raise DataProtectionError("Remote identity path changed to a symbolic link")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\\0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            elif os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                raise RemoteIdentityError("Remote identity requires OS file locking")
            acquired = True
            if _contains_symlink_component(path) or _contains_symlink_component(lock_path):
                raise DataProtectionError("Remote identity path changed while acquiring its lock")
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RemoteIdentityStore:
    """Process/thread-safe encrypted Ed25519 identity with explicit bootstrap."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else IDENTITY_FILE

    @contextmanager
    def _guard(self) -> Iterator[None]:
        with _PROCESS_LOCK:
            with _identity_file_lock(self.path):
                yield

    @staticmethod
    def _raw_public_key(key: Ed25519PublicKey) -> bytes:
        return key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def _read_unlocked(self) -> tuple[dict[str, Any], Ed25519PrivateKey] | None:
        if _contains_symlink_component(self.path):
            raise DataProtectionError("Remote identity path must not contain symbolic links")
        if not self.path.exists():
            return None
        if self.path.stat().st_size > _MAX_IDENTITY_FILE_BYTES:
            raise RemoteIdentityError("Remote identity record exceeds its size limit")
        encrypted = self.path.read_bytes()
        if not is_encrypted_bytes(encrypted):
            raise RemoteIdentityError(
                "Remote identity record is not authenticated-encrypted; refusing plaintext migration"
            )
        from agent.storage import load_json

        try:
            record = load_json(self.path, default=None, encrypt=True)
        except (DataProtectionError, ValueError, OSError) as error:
            raise RemoteIdentityError("Remote identity record could not be authenticated or read") from error
        required = {
            "schema_version", "identity_id", "algorithm", "created_at",
            "public_key", "private_key", "fingerprint_sha256",
        }
        if not isinstance(record, dict) or set(record) != required:
            raise RemoteIdentityError("Remote identity record has an unsupported schema")
        if record.get("schema_version") != _SCHEMA_VERSION or record.get("algorithm") != "Ed25519":
            raise RemoteIdentityError("Remote identity algorithm or schema is unsupported")
        try:
            uuid.UUID(hex=record["identity_id"])
            created = datetime.fromisoformat(record["created_at"])
            if created.tzinfo is None:
                raise ValueError("created_at must be timezone-aware")
            private_raw = _b64decode(record["private_key"], "private key")
            public_raw = _b64decode(record["public_key"], "public key")
            if len(private_raw) != 32 or len(public_raw) != 32:
                raise ValueError("invalid Ed25519 key length")
            private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
            actual_public = self._raw_public_key(private_key.public_key())
            actual_fingerprint = hashlib.sha256(actual_public).hexdigest()
            if not hmac.compare_digest(actual_public, public_raw):
                raise ValueError("public/private key mismatch")
            if not hmac.compare_digest(actual_fingerprint, str(record["fingerprint_sha256"])):
                raise ValueError("fingerprint mismatch")
        except (KeyError, TypeError, ValueError) as error:
            raise RemoteIdentityError("Remote identity key material failed validation") from error
        return record, private_key

    def status(self) -> dict[str, Any]:
        """Return non-secret identity metadata; never return private key material."""
        with self._guard():
            loaded = self._read_unlocked()
            if loaded is None:
                return {"initialized": False, "algorithm": "Ed25519"}
            record, _private_key = loaded
            return {
                "initialized": True,
                "identity_id": record["identity_id"],
                "algorithm": record["algorithm"],
                "created_at": record["created_at"],
                "public_key": record["public_key"],
                "fingerprint_sha256": record["fingerprint_sha256"],
            }

    def bootstrap(self, *, confirmed: bool = False) -> dict[str, Any]:
        """Create the first identity only after an explicit owner confirmation."""
        if confirmed is not True:
            raise PermissionError("Explicit owner confirmation is required to create the desktop identity")
        with self._guard():
            if self.path.exists() or self.path.is_symlink():
                raise RemoteIdentityError(
                    "A remote identity record already exists; it will not be overwritten or silently rotated"
                )
            private_key = Ed25519PrivateKey.generate()
            private_raw = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            public_raw = self._raw_public_key(private_key.public_key())
            record = {
                "schema_version": _SCHEMA_VERSION,
                "identity_id": uuid.uuid4().hex,
                "algorithm": "Ed25519",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "public_key": _b64encode(public_raw),
                "private_key": _b64encode(private_raw),
                "fingerprint_sha256": hashlib.sha256(public_raw).hexdigest(),
            }
            atomic_write_json(self.path, record, indent=2, encrypt=True)
            # Read back and authenticate the committed record before reporting success.
            loaded = self._read_unlocked()
            if loaded is None:
                raise RemoteIdentityError("Desktop identity write could not be verified")
            verified, _ = loaded
            if verified["identity_id"] != record["identity_id"]:
                raise RemoteIdentityError("Desktop identity changed during bootstrap")
            return {
                key: value for key, value in verified.items() if key != "private_key"
            }

    def sign(self, message: bytes) -> bytes:
        if not isinstance(message, bytes) or len(message) > _MAX_MESSAGE_BYTES:
            raise ValueError("Signed message must be bytes no larger than 1 MiB")
        with self._guard():
            loaded = self._read_unlocked()
            if loaded is None:
                raise RemoteIdentityError("Desktop identity is not initialized")
            _record, private_key = loaded
            return private_key.sign(message)

    @staticmethod
    def verify(public_key: str, message: bytes, signature: bytes) -> bool:
        if not isinstance(message, bytes) or len(message) > _MAX_MESSAGE_BYTES:
            return False
        try:
            raw = _b64decode(public_key, "public key")
            if len(raw) != 32 or not isinstance(signature, bytes) or len(signature) != 64:
                return False
            Ed25519PublicKey.from_public_bytes(raw).verify(signature, message)
            return True
        except (RemoteIdentityError, InvalidSignature, ValueError, TypeError):
            return False


def get_remote_identity_store() -> RemoteIdentityStore:
    """Return the process-wide default identity store."""
    return RemoteIdentityStore()
