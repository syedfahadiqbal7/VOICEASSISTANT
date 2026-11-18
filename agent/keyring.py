"""Versioned protected keyring primitives for Phase 8.3.

This module is deliberately not wired into data_protection.py yet. It establishes
and tests the keyring format before any production ciphertext format is changed.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = Path(".").resolve()

KEYRING_FILE = Path(BASE_DIR) / "history" / ".voiceassistant_keyring.v1"
_SCHEMA_VERSION = 1
_KEY_SIZE = 32
_NONCE_SIZE = 12
_ENV_MAGIC = b"VAKR1E"
_DPAPI_MAGIC = b"VAKR1D"
_ENV_SALT = b"VoiceAssistant.Keyring.Salt.v1"
_ENV_INFO = b"VoiceAssistant.Keyring.Wrap.v1"
_DPAPI_ENTROPY = b"VoiceAssistant.Keyring.Protection.v1"
_KEY_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.RLock()


class KeyringError(RuntimeError):
    """Raised when a protected keyring is unavailable or invalid."""


def _protection_source(source: str | None = None) -> str:
    if source is not None:
        if source not in {"environment", "windows-dpapi"}:
            raise KeyringError("Unsupported keyring protection source")
        if source == "windows-dpapi" and os.name != "nt":
            raise KeyringError("Windows DPAPI is unavailable on this platform")
        return source
    if os.environ.get("VOICEASSISTANT_DATA_KEY", "").strip():
        return "environment"
    if os.name == "nt":
        return "windows-dpapi"
    raise KeyringError(
        "Keyring protection requires VOICEASSISTANT_DATA_KEY or Windows DPAPI"
    )


def _environment_wrap_key() -> bytes:
    configured = os.environ.get("VOICEASSISTANT_DATA_KEY", "").strip()
    if not configured:
        raise KeyringError("VOICEASSISTANT_DATA_KEY is required to unlock this keyring")
    try:
        if len(configured) == 64:
            master = bytes.fromhex(configured)
        else:
            padded = configured + ("=" * (-len(configured) % 4))
            master = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except Exception as error:
        raise KeyringError("VOICEASSISTANT_DATA_KEY is malformed") from error
    if len(master) != _KEY_SIZE:
        raise KeyringError("VOICEASSISTANT_DATA_KEY must decode to exactly 32 bytes")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_SIZE,
        salt=_ENV_SALT,
        info=_ENV_INFO,
    ).derive(master)


def _protect(payload: bytes, source: str) -> bytes:
    if source == "environment":
        nonce = os.urandom(_NONCE_SIZE)
        encrypted = AESGCM(_environment_wrap_key()).encrypt(
            nonce, payload, b"VoiceAssistant.Keyring.v1"
        )
        return _ENV_MAGIC + nonce + encrypted
    try:
        import win32crypt
        protected = win32crypt.CryptProtectData(
            payload, "VoiceAssistant keyring", _DPAPI_ENTROPY, None, None, 0x1
        )
        return _DPAPI_MAGIC + protected
    except Exception as error:
        raise KeyringError("Could not protect keyring with Windows DPAPI") from error


def _unprotect(payload: bytes, source: str) -> bytes:
    if source == "environment":
        if not payload.startswith(_ENV_MAGIC):
            raise KeyringError("Keyring protection format does not match environment mode")
        offset = len(_ENV_MAGIC)
        if len(payload) < offset + _NONCE_SIZE + 16:
            raise KeyringError("Protected keyring is truncated")
        nonce = payload[offset:offset + _NONCE_SIZE]
        try:
            return AESGCM(_environment_wrap_key()).decrypt(
                nonce, payload[offset + _NONCE_SIZE:],
                b"VoiceAssistant.Keyring.v1",
            )
        except InvalidTag as error:
            raise KeyringError("Keyring authentication failed; wrong unlock key or tampering") from error
        except KeyringError:
            raise
        except Exception as error:
            raise KeyringError("Could not unlock protected keyring") from error

    if not payload.startswith(_DPAPI_MAGIC) or len(payload) <= len(_DPAPI_MAGIC):
        raise KeyringError("Keyring protection format does not match Windows DPAPI mode")
    try:
        import win32crypt
        result = win32crypt.CryptUnprotectData(
            payload[len(_DPAPI_MAGIC):], _DPAPI_ENTROPY, None, None, 0x1
        )
        clear = result[1] if isinstance(result, tuple) else result
        if not isinstance(clear, bytes):
            raise KeyringError("DPAPI returned an invalid keyring payload")
        return clear
    except KeyringError:
        raise
    except Exception as error:
        raise KeyringError("Could not unlock keyring with this Windows user profile") from error


def validate_keyring(value: Any) -> dict:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value.get("schema_version") != _SCHEMA_VERSION:
        raise KeyringError("Unsupported or malformed keyring schema")
    active_id = value.get("active_key_id")
    keys = value.get("keys")
    if not isinstance(active_id, str) or not _KEY_ID_RE.fullmatch(active_id):
        raise KeyringError("Keyring active key ID is invalid")
    if not isinstance(keys, dict) or not keys or active_id not in keys:
        raise KeyringError("Keyring has no valid active key")
    normalized = {}
    for key_id, entry in keys.items():
        if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
            raise KeyringError("Keyring contains an invalid key ID")
        if not isinstance(entry, dict):
            raise KeyringError("Keyring key entry is malformed")
        encoded = entry.get("key")
        if not isinstance(encoded, str):
            raise KeyringError("Keyring key material is missing")
        try:
            key = base64.b64decode(encoded.encode("ascii"), validate=True)
        except Exception as error:
            raise KeyringError("Keyring key material is malformed") from error
        if len(key) != _KEY_SIZE:
            raise KeyringError("Keyring contains a key with an invalid length")
        created_at = entry.get("created_at")
        if not isinstance(created_at, str) or not created_at:
            raise KeyringError("Keyring key creation timestamp is missing")
        state = entry.get("state")
        if not isinstance(state, str) or state not in {"active", "retained", "legacy"}:
            raise KeyringError("Keyring key state is invalid")
        normalized[key_id] = {
            "key": encoded,
            "created_at": created_at,
            "state": state,
        }
    if normalized[active_id]["state"] != "active":
        raise KeyringError("Active key entry is not marked active")
    if sum(1 for entry in normalized.values() if entry["state"] == "active") != 1:
        raise KeyringError("Keyring must contain exactly one active key")
    return {
        "schema_version": _SCHEMA_VERSION,
        "active_key_id": active_id,
        "keys": normalized,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        os.replace(temporary, path)
        temporary = None
        if os.name != "nt":
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                # Some filesystems do not support directory fsync.
                pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def save_keyring(value: dict, path: Path | str | None = None,
                 source: str | None = None) -> None:
    target = Path(path or KEYRING_FILE)
    selected_source = _protection_source(source)
    validated = validate_keyring(value)
    clear = json.dumps(validated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    protected = _protect(clear, selected_source)
    with _lock:
        previous = target.read_bytes() if target.is_file() else None
        replaced = False
        try:
            _atomic_write(target, protected)
            replaced = True
            # Read-after-write verification prevents reporting success for an
            # unreadable/corrupt keyring.
            reopened = load_keyring(target, source=selected_source)
            if reopened != validated:
                raise KeyringError("Keyring verification after persistence failed")
        except Exception:
            # Never strand the application with a newly-written keyring that
            # cannot be reopened. Restore the prior bytes when one existed.
            if replaced:
                if previous is None:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                else:
                    _atomic_write(target, previous)
            raise


def load_keyring(path: Path | str | None = None,
                 source: str | None = None) -> dict:
    target = Path(path or KEYRING_FILE)
    selected_source = _protection_source(source)
    try:
        protected = target.read_bytes()
    except OSError as error:
        raise KeyringError("Protected keyring is unavailable") from error
    clear = _unprotect(protected, selected_source)
    try:
        value = json.loads(clear.decode("utf-8"))
    except Exception as error:
        raise KeyringError("Protected keyring contents are malformed") from error
    return validate_keyring(value)


def create_initial_keyring(legacy_key: bytes, path: Path | str | None = None,
                           source: str | None = None) -> dict:
    """Create a keyring retaining the current key and a fresh active DEK.

    This is an explicit bootstrap primitive. It never overwrites an existing
    keyring and does not alter existing encrypted files.
    """
    if not isinstance(legacy_key, bytes) or len(legacy_key) != _KEY_SIZE:
        raise KeyringError("Legacy data key must be exactly 32 bytes")
    target = Path(path or KEYRING_FILE)
    with _lock:
        if target.exists():
            raise KeyringError("A keyring already exists; refusing to overwrite it")
        now = datetime.now(timezone.utc).isoformat()
        legacy_id = os.urandom(16).hex()
        active_id = os.urandom(16).hex()
        value = {
            "schema_version": _SCHEMA_VERSION,
            "active_key_id": active_id,
            "keys": {
                legacy_id: {
                    "key": base64.b64encode(legacy_key).decode("ascii"),
                    "created_at": now,
                    "state": "legacy",
                },
                active_id: {
                    "key": base64.b64encode(os.urandom(_KEY_SIZE)).decode("ascii"),
                    "created_at": now,
                    "state": "active",
                },
            },
        }
        save_keyring(value, target, source=source)
        return load_keyring(target, source=source)


def get_key(keyring: dict, key_id: str) -> bytes:
    validated = validate_keyring(keyring)
    try:
        return base64.b64decode(validated["keys"][key_id]["key"], validate=True)
    except KeyError as error:
        raise KeyringError("Requested key ID is not present in keyring") from error


def get_active_key(keyring: dict) -> tuple[str, bytes]:
    validated = validate_keyring(keyring)
    key_id = validated["active_key_id"]
    return key_id, get_key(validated, key_id)

def get_legacy_key(keyring: dict) -> bytes:
    """Return the single preserved VAENC1 key; ambiguity fails closed."""
    validated = validate_keyring(keyring)
    legacy_ids = [
        key_id for key_id, entry in validated["keys"].items()
        if entry["state"] == "legacy"
    ]
    if len(legacy_ids) != 1:
        raise KeyringError(
            "Keyring must contain exactly one preserved legacy key for VAENC1 data"
        )
    return get_key(validated, legacy_ids[0])

