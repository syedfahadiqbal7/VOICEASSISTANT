"""Authenticated encryption for local VoiceAssistant data stores.

Windows uses a random AES-256 key protected by the current user's DPAPI profile.
Other platforms must provide VOICEASSISTANT_DATA_KEY (32-byte hex or base64).
There is deliberately no plaintext fallback when encryption is requested.
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
_MAGIC = b"VAENC1"
_VERSION = 1
_NONCE_SIZE = 12
_KEY_SIZE = 32
_DPAPI_ENTROPY = b"VoiceAssistant.LocalData.Key.v1"
_lock = threading.RLock()


class DataProtectionError(RuntimeError):
    """Raised when encrypted local data cannot be safely read or written."""


def _aad(context: str) -> bytes:
    label = str(context or "").strip()
    if not label:
        raise DataProtectionError("An encryption context is required")
    return b"VoiceAssistant.LocalData.v1:" + label.encode("utf-8")


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
            # Another process initialized the key concurrently.
            return _unprotect_dpapi(path.read_bytes())
        except Exception as error:
            raise DataProtectionError("Could not create the DPAPI-protected data key") from error


def get_data_key() -> Tuple[bytes, str]:
    """Return the active 256-bit key and its protection source."""
    configured = os.environ.get("VOICEASSISTANT_DATA_KEY", "").strip()
    if configured:
        return _environment_key(configured), "environment"
    if os.name == "nt":
        return _windows_dpapi_key(), "windows-dpapi"
    raise DataProtectionError(
        "Encrypted local storage needs VOICEASSISTANT_DATA_KEY on non-Windows systems"
    )


def is_encrypted_bytes(payload: bytes) -> bool:
    return isinstance(payload, bytes) and payload.startswith(_MAGIC)


def encrypt_bytes(plaintext: bytes, context: str) -> bytes:
    if not isinstance(plaintext, bytes):
        raise TypeError("plaintext must be bytes")
    key, _source = get_data_key()
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(context))
    return _MAGIC + bytes([_VERSION]) + nonce + ciphertext


def decrypt_bytes(payload: bytes, context: str) -> bytes:
    if not is_encrypted_bytes(payload):
        raise DataProtectionError("File is not a VoiceAssistant encrypted payload")
    header_size = len(_MAGIC) + 1 + _NONCE_SIZE
    if len(payload) <= header_size:
        raise DataProtectionError("Encrypted payload is truncated")
    version = payload[len(_MAGIC)]
    if version != _VERSION:
        raise DataProtectionError(f"Unsupported encrypted payload version: {version}")
    nonce_start = len(_MAGIC) + 1
    nonce = payload[nonce_start:header_size]
    ciphertext = payload[header_size:]
    key, _source = get_data_key()
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _aad(context))
    except InvalidTag as error:
        raise DataProtectionError(
            "Encrypted data authentication failed (wrong key, wrong file context or tampering)"
        ) from error
    except Exception as error:
        raise DataProtectionError(f"Could not decrypt local data: {error}") from error
