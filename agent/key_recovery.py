"""Passphrase-protected, local-only export/import of the complete data keyring."""
from __future__ import annotations

import json
import os
import struct
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from agent.key_admin_lock import KeyAdminLock, KeyAdminLockError
from agent.keyring import (
    KeyringError, _protection_source, load_keyring, save_keyring, validate_keyring,
)

_MAGIC = b"VARECOV"
_VERSION = 1
_ITERATIONS = 600_000
_MIN_ITERATIONS = 600_000
_MAX_ITERATIONS = 1_000_000
_SALT_SIZE = 16
_NONCE_SIZE = 12
_KEY_SIZE = 32
_MAX_KIT_BYTES = 1024 * 1024
_AAD_PREFIX = b"VoiceAssistant.DataKeyRecovery.v1"


class RecoveryKitError(RuntimeError):
    """Raised when a recovery kit cannot safely be exported or imported."""


def _derive(passphrase: str, salt: bytes, iterations: int) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 16:
        raise RecoveryKitError("Recovery passphrase must contain at least 16 characters")
    if type(iterations) is not int or not (_MIN_ITERATIONS <= iterations <= _MAX_ITERATIONS):
        raise RecoveryKitError("Recovery kit KDF work factor is outside the accepted safety limits")
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=_KEY_SIZE,
        salt=salt, iterations=iterations,
    ).derive(passphrase.encode("utf-8"))


def _history_and_keyring(base_dir: Path | str) -> tuple[Path, Path]:
    root = Path(base_dir).resolve(strict=True)
    history = root / "history"
    if history.is_symlink():
        raise RecoveryKitError("Runtime history directory must not be a symbolic link")
    history.mkdir(parents=True, exist_ok=True)
    from agent.key_rotation import _keyring_path
    return history, _keyring_path(history)


def _atomic_create(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise FileExistsError("Recovery kit destination already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        # Hard-link creation is atomic and fails if another process created the
        # destination after our initial existence check.
        os.link(temporary, path)
        os.unlink(temporary)
        temporary = None
        if os.name != "nt":
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _encode(ring: dict, passphrase: str) -> bytes:
    salt = os.urandom(_SALT_SIZE)
    nonce = os.urandom(_NONCE_SIZE)
    header = _MAGIC + bytes([_VERSION]) + struct.pack(">I", _ITERATIONS) + salt + nonce
    clear = json.dumps(validate_keyring(ring), sort_keys=True, separators=(",", ":")).encode("utf-8")
    encrypted = AESGCM(_derive(passphrase, salt, _ITERATIONS)).encrypt(
        nonce, clear, _AAD_PREFIX + header
    )
    return header + encrypted


def _decode(payload: bytes, passphrase: str) -> dict:
    header_size = len(_MAGIC) + 1 + 4 + _SALT_SIZE + _NONCE_SIZE
    if len(payload) > _MAX_KIT_BYTES or len(payload) <= header_size + 16:
        raise RecoveryKitError("Recovery kit is oversized, truncated, or empty")
    if not payload.startswith(_MAGIC):
        raise RecoveryKitError("Not a VoiceAssistant recovery kit")
    offset = len(_MAGIC)
    version = payload[offset]
    if version != _VERSION:
        raise RecoveryKitError(f"Unsupported recovery kit version: {version}")
    iterations = struct.unpack(">I", payload[offset + 1:offset + 5])[0]
    salt_start = offset + 5
    salt = payload[salt_start:salt_start + _SALT_SIZE]
    nonce_start = salt_start + _SALT_SIZE
    nonce = payload[nonce_start:nonce_start + _NONCE_SIZE]
    header = payload[:header_size]
    try:
        clear = AESGCM(_derive(passphrase, salt, iterations)).decrypt(
            nonce, payload[header_size:], _AAD_PREFIX + header
        )
    except InvalidTag as error:
        raise RecoveryKitError("Recovery kit authentication failed (wrong passphrase or tampering)") from error
    except (ValueError, TypeError) as error:
        raise RecoveryKitError("Recovery kit could not be decrypted") from error
    try:
        value = json.loads(clear.decode("utf-8"))
        return validate_keyring(value)
    except (UnicodeError, ValueError, TypeError, KeyringError) as error:
        raise RecoveryKitError("Authenticated recovery kit contains an invalid keyring") from error


def export_recovery_kit(
    base_dir: Path | str, destination: Path | str, passphrase: str,
) -> dict:
    """Export all active, retained, and legacy keys in a separate encrypted artifact."""
    history, keyring_path = _history_and_keyring(base_dir)
    target = Path(destination).expanduser()
    if target.is_symlink():
        raise RecoveryKitError("Recovery kit destination must not be a symbolic link")
    try:
        with KeyAdminLock(history):
            ring = load_keyring(keyring_path)
            payload = _encode(ring, passphrase)
            _atomic_create(target, payload)
            # Authenticate the exact bytes persisted before reporting success.
            reopened = _decode(target.read_bytes(), passphrase)
            if reopened != ring:
                raise RecoveryKitError("Exported recovery kit verification failed")
            return {
                "ok": True, "operation": "export",
                "key_count": len(ring["keys"]),
                "active_key_id": ring["active_key_id"],
                "bytes_written": len(payload),
            }
    except (KeyAdminLockError, KeyringError, OSError) as error:
        raise RecoveryKitError(f"Recovery-kit export failed safely: {error}") from error


def import_recovery_kit(
    base_dir: Path | str, source: Path | str, passphrase: str,
    *, confirm: bool = False,
) -> dict:
    """Restore only into an installation with no keyring; never overwrite a keyring."""
    if not confirm:
        raise PermissionError("Recovery-kit import requires explicit local confirmation")
    history, keyring_path = _history_and_keyring(base_dir)
    kit = Path(source).expanduser()
    if kit.is_symlink() or not kit.is_file():
        raise RecoveryKitError("Recovery kit must be a regular non-symbolic-link file")
    try:
        before = kit.stat()
        if before.st_size > _MAX_KIT_BYTES:
            raise RecoveryKitError("Recovery kit exceeds the 1 MiB safety limit")
        payload = kit.read_bytes()
        after = kit.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino
        ):
            raise RecoveryKitError("Recovery kit changed while being read")
        ring = _decode(payload, passphrase)
        with KeyAdminLock(history):
            if keyring_path.exists() or keyring_path.is_symlink():
                raise RecoveryKitError("A keyring already exists; refusing to overwrite it")
            selected_source = _protection_source()
            save_keyring(ring, keyring_path, source=selected_source)
            reopened = load_keyring(keyring_path, source=selected_source)
            if reopened != ring:
                # Do not delete or roll back a keyring after a possible durable
                # write; leave it in place for operator inspection.
                raise RecoveryKitError("Imported keyring read-back verification failed")
            return {
                "ok": True, "operation": "import",
                "key_count": len(reopened["keys"]),
                "active_key_id": reopened["active_key_id"],
                "protection": selected_source,
            }
    except (KeyAdminLockError, KeyringError, OSError) as error:
        raise RecoveryKitError(f"Recovery-kit import failed safely: {error}") from error
