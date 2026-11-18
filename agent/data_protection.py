"""Authenticated encryption for local VoiceAssistant data stores.

VAENC1 remains readable with the original single data key. Once an operator
explicitly bootstraps a protected keyring, new writes use VAENC2 and its active
key ID; no keyring is silently created by normal application writes.
"""
from __future__ import annotations

import base64
import os
import threading
from pathlib import Path
from typing import Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from config import BASE_DIR
except Exception:
    BASE_DIR = Path(".").resolve()

DATA_KEY_FILE = Path(BASE_DIR) / "history" / ".voiceassistant_data_key.dpapi"
_MAGIC_V1 = b"VAENC1"
_MAGIC_V2 = b"VAENC2"
_VERSION_V1 = 1
_VERSION_V2 = 2
_NONCE_SIZE = 12
_KEY_SIZE = 32
_KEY_ID_SIZE = 16
_DPAPI_ENTROPY = b"VoiceAssistant.LocalData.Key.v1"
_lock = threading.RLock()


class DataProtectionError(RuntimeError):
    """Raised when encrypted local data cannot be safely read or written."""


def _aad(context: str) -> bytes:
    label = str(context or "").strip()
    if not label:
        raise DataProtectionError("An encryption context is required")
    return b"VoiceAssistant.LocalData.v1:" + label.encode("utf-8")


def _aad_v2(context: str, key_id: bytes) -> bytes:
    label = str(context or "").strip()
    if not label:
        raise DataProtectionError("An encryption context is required")
    if not isinstance(key_id, bytes) or len(key_id) != _KEY_ID_SIZE:
        raise DataProtectionError("VAENC2 key ID is invalid")
    return b"VoiceAssistant.LocalData.v2:" + label.encode("utf-8") + b":" + key_id


def _environment_key(value: str) -> bytes:
    candidate = value.strip()
    try:
        if len(candidate) == 64:
            key = bytes.fromhex(candidate)
        else:
            padded = candidate + ("=" * (-len(candidate) % 4))
            key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as error:
        raise DataProtectionError(
            "VOICEASSISTANT_DATA_KEY must be a 32-byte hexadecimal or base64 value"
        ) from error
    if len(key) != _KEY_SIZE:
        raise DataProtectionError("VOICEASSISTANT_DATA_KEY must decode to exactly 32 bytes")
    return key


def _unprotect_dpapi(blob: bytes) -> bytes:
    try:
        import win32crypt
        result = win32crypt.CryptUnprotectData(
            blob, _DPAPI_ENTROPY, None, None, 0x1
        )
        key = result[1] if isinstance(result, tuple) else result
    except Exception as error:
        raise DataProtectionError(
            "Could not unlock the local data key with this Windows user profile"
        ) from error
    if not isinstance(key, bytes) or len(key) != _KEY_SIZE:
        raise DataProtectionError("The DPAPI-protected local data key is invalid")
    return key


def _windows_dpapi_key() -> bytes:
    path = Path(DATA_KEY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32crypt
    except Exception as error:
        raise DataProtectionError(
            "pywin32 is required for Windows DPAPI data protection"
        ) from error

    with _lock:
        if path.is_file():
            return _unprotect_dpapi(path.read_bytes())

        key = os.urandom(_KEY_SIZE)
        try:
            protected = win32crypt.CryptProtectData(
                key, "VoiceAssistant local data key", _DPAPI_ENTROPY, None, None, 0x1
            )
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(protected)
                handle.flush()
                os.fsync(handle.fileno())
            return key
        except FileExistsError:
            return _unprotect_dpapi(path.read_bytes())
        except Exception as error:
            raise DataProtectionError("Could not create the DPAPI-protected data key") from error


def get_data_key() -> Tuple[bytes, str]:
    """Return the original 256-bit VAENC1 key and its protection source."""
    configured = os.environ.get("VOICEASSISTANT_DATA_KEY", "").strip()
    if configured:
        return _environment_key(configured), "environment"
    if os.name == "nt":
        return _windows_dpapi_key(), "windows-dpapi"
    raise DataProtectionError(
        "Encrypted local storage needs VOICEASSISTANT_DATA_KEY on non-Windows systems"
    )


def _load_existing_keyring():
    """Load a keyring only when one already exists; never bootstrap implicitly."""
    try:
        from agent import keyring as keyring_module
        path = Path(keyring_module.KEYRING_FILE)
        if not path.is_file():
            return None, keyring_module
        return keyring_module.load_keyring(path), keyring_module
    except Exception as error:
        if isinstance(error, DataProtectionError):
            raise
        raise DataProtectionError(f"Could not load protected data keyring: {error}") from error


def is_encrypted_bytes(payload: bytes) -> bool:
    return isinstance(payload, bytes) and (
        payload.startswith(_MAGIC_V1) or payload.startswith(_MAGIC_V2)
    )


def encrypt_bytes(plaintext: bytes, context: str) -> bytes:
    """Encrypt using VAENC2 after explicit keyring bootstrap, otherwise VAENC1."""
    if not isinstance(plaintext, bytes):
        raise TypeError("plaintext must be bytes")
    keyring, module = _load_existing_keyring()
    if keyring is None:
        key, _source = get_data_key()
        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(context))
        return _MAGIC_V1 + bytes([_VERSION_V1]) + nonce + ciphertext

    try:
        key_id, key = module.get_active_key(keyring)
        key_id_bytes = bytes.fromhex(key_id)
        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = AESGCM(key).encrypt(
            nonce, plaintext, _aad_v2(context, key_id_bytes)
        )
        return _MAGIC_V2 + bytes([_VERSION_V2]) + key_id_bytes + nonce + ciphertext
    except Exception as error:
        if isinstance(error, DataProtectionError):
            raise
        raise DataProtectionError(f"Could not encrypt using the active keyring key: {error}") from error


def decrypt_bytes(payload: bytes, context: str) -> bytes:
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if payload.startswith(_MAGIC_V1):
        header_size = len(_MAGIC_V1) + 1 + _NONCE_SIZE
        if len(payload) <= header_size:
            raise DataProtectionError("VAENC1 encrypted payload is truncated")
        version = payload[len(_MAGIC_V1)]
        if version != _VERSION_V1:
            raise DataProtectionError(f"Unsupported VAENC1 encrypted payload version: {version}")
        nonce_start = len(_MAGIC_V1) + 1
        nonce = payload[nonce_start:header_size]
        ciphertext = payload[header_size:]
        keyring, module = _load_existing_keyring()
        key = module.get_legacy_key(keyring) if keyring is not None else get_data_key()[0]
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, _aad(context))
        except InvalidTag as error:
            raise DataProtectionError(
                "VAENC1 authentication failed (wrong legacy key, context or tampering)"
            ) from error
        except Exception as error:
            raise DataProtectionError(f"Could not decrypt VAENC1 data: {error}") from error

    if not payload.startswith(_MAGIC_V2):
        raise DataProtectionError("File is not a supported VoiceAssistant encrypted payload")

    header_size = len(_MAGIC_V2) + 1 + _KEY_ID_SIZE + _NONCE_SIZE
    if len(payload) <= header_size:
        raise DataProtectionError("VAENC2 encrypted payload is truncated")
    version = payload[len(_MAGIC_V2)]
    if version != _VERSION_V2:
        raise DataProtectionError(f"Unsupported VAENC2 encrypted payload version: {version}")
    key_id_start = len(_MAGIC_V2) + 1
    key_id_bytes = payload[key_id_start:key_id_start + _KEY_ID_SIZE]
    nonce_start = key_id_start + _KEY_ID_SIZE
    nonce = payload[nonce_start:header_size]
    ciphertext = payload[header_size:]

    keyring, module = _load_existing_keyring()
    if keyring is None:
        raise DataProtectionError("VAENC2 data exists but the protected keyring is unavailable")
    try:
        key_id = key_id_bytes.hex()
        key = module.get_key(keyring, key_id)
        return AESGCM(key).decrypt(
            nonce, ciphertext, _aad_v2(context, key_id_bytes)
        )
    except InvalidTag as error:
        raise DataProtectionError(
            "VAENC2 authentication failed (wrong key, context or tampering)"
        ) from error
    except Exception as error:
        if isinstance(error, DataProtectionError):
            raise
        raise DataProtectionError(f"Could not decrypt VAENC2 data: {error}") from error
