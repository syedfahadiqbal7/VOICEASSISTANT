"""Encrypted local registry for OAuth-backed provider connections.

Tokens are written only to an authenticated-encrypted store. Status APIs never
return token material, and supported scopes are allow-listed per provider.
OAuth acquisition and refresh are handled by provider adapters, not this store.
"""
from __future__ import annotations

import copy
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set

from config import BASE_DIR
from agent.data_protection import DataProtectionError
from agent.storage import atomic_write_json, load_json


INTEGRATION_FILE = Path(BASE_DIR) / "history" / "integrations.json"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TOKEN_MIN = 8
_TOKEN_MAX = 8192
_MAX_SCOPES = 32
_MAX_STORE_BYTES = 256 * 1024

_PROVIDER_SCOPES = {
    "google": {
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    },
    "microsoft": {
        "openid",
        "profile",
        "email",
        "offline_access",
        "User.Read",
        "Mail.Read",
        "Calendars.Read",
    },
}
_CAPABILITY_SCOPES = {
    "email": {
        "google": "https://www.googleapis.com/auth/gmail.readonly",
        "microsoft": "Mail.Read",
    },
    "calendar": {
        "google": "https://www.googleapis.com/auth/calendar.readonly",
        "microsoft": "Calendars.Read",
    },
}


class IntegrationStoreError(RuntimeError):
    """Raised when an integration connection cannot be safely stored or read."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_expiry(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expires_at must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include an explicit timezone offset")
    return parsed.astimezone(timezone.utc)


def _contains_symlink_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(str(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if current.is_symlink():
            return True
    return False


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    if _contains_symlink_component(path) or _contains_symlink_component(lock_path):
        raise DataProtectionError("Integration store and lock paths must not contain symbolic links")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if _contains_symlink_component(path) or _contains_symlink_component(lock_path):
        raise DataProtectionError("Integration store path changed to a symbolic link")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
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
                raise RuntimeError("Integration storage requires OS file locking")
            acquired = True
            if _contains_symlink_component(path) or _contains_symlink_component(lock_path):
                raise DataProtectionError("Integration store path changed while acquiring its lock")
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class IntegrationConnectionStore:
    """Thread/process-safe encrypted store for Google and Microsoft OAuth tokens."""

    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path or INTEGRATION_FILE)
        if _contains_symlink_component(self.path):
            raise DataProtectionError("Integration store path must not contain symbolic links")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _contains_symlink_component(self.path):
            raise DataProtectionError("Integration store path changed to a symbolic link")
        self._lock = threading.RLock()
        with self._guard():
            self._read_unlocked()

    @contextmanager
    def _guard(self):
        with self._lock:
            with _file_lock(self.path):
                yield

    def _read_unlocked(self) -> Dict[str, Any]:
        if _contains_symlink_component(self.path):
            raise DataProtectionError("Integration store path must not contain symbolic links")
        if self.path.exists() and self.path.stat().st_size > _MAX_STORE_BYTES:
            raise IntegrationStoreError("Integration store exceeds its size limit")
        document = load_json(
            self.path,
            default={"version": 1, "connections": {}},
            encrypt=True,
        )
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or not isinstance(document.get("connections"), dict)
        ):
            raise IntegrationStoreError("Unsupported or malformed integration store")
        connections = document["connections"]
        if set(connections) - set(_PROVIDER_SCOPES):
            raise IntegrationStoreError("Integration store contains an unsupported provider")
        for provider, record in connections.items():
            self._validate_record(provider, record)
        return document

    @staticmethod
    def _validate_record(provider: str, record: Any) -> Dict[str, Any]:
        if provider not in _PROVIDER_SCOPES or not isinstance(record, dict):
            raise IntegrationStoreError("Invalid provider connection record")
        allowed = {
            "account_email", "scopes", "access_token", "refresh_token",
            "expires_at", "connected_at",
        }
        if set(record) != allowed:
            raise IntegrationStoreError("Integration connection fields do not match the schema")
        email = record.get("account_email")
        if not isinstance(email, str) or not _EMAIL_RE.fullmatch(email) or len(email) > 254:
            raise IntegrationStoreError("Integration account email is invalid")
        for field in ("access_token", "refresh_token"):
            token = record.get(field)
            if not isinstance(token, str) or not (_TOKEN_MIN <= len(token) <= _TOKEN_MAX):
                raise IntegrationStoreError("Integration token is invalid")
        scopes = record.get("scopes")
        if (
            not isinstance(scopes, list)
            or not scopes
            or len(scopes) > _MAX_SCOPES
            or not all(isinstance(scope, str) and scope in _PROVIDER_SCOPES[provider] for scope in scopes)
            or len(set(scopes)) != len(scopes)
        ):
            raise IntegrationStoreError("Integration scopes are invalid or not allow-listed")
        if not any(
            required in scopes
            for required in _CAPABILITY_SCOPES["email"].values()
            if required in _PROVIDER_SCOPES[provider]
        ) and not any(
            required in scopes
            for required in _CAPABILITY_SCOPES["calendar"].values()
            if required in _PROVIDER_SCOPES[provider]
        ):
            raise IntegrationStoreError("Connection has no supported mail or calendar permission")
        try:
            _parse_expiry(record.get("expires_at"))
            connected_at = record.get("connected_at")
            if not isinstance(connected_at, str) or not connected_at:
                raise ValueError("connected_at is invalid")
            connected_dt = datetime.fromisoformat(connected_at.replace("Z", "+00:00"))
            if connected_dt.tzinfo is None:
                raise ValueError("connected_at must include an explicit timezone")
        except (TypeError, ValueError, OverflowError) as error:
            raise IntegrationStoreError("Integration timestamps are invalid") from error
        return dict(record)

    def save_connection(
        self,
        provider: str,
        account_email: str,
        scopes: list[str],
        access_token: str,
        refresh_token: str,
        expires_at: str,
    ) -> Dict[str, Any]:
        provider = str(provider or "").strip().lower()
        if provider not in _PROVIDER_SCOPES:
            raise ValueError("Provider must be google or microsoft")
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise ValueError("Scopes must be a list of strings")
        record = {
            "account_email": str(account_email or "").strip(),
            "scopes": sorted(set(scopes)),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "connected_at": _utc_now(),
        }
        self._validate_record(provider, record)
        with self._guard():
            document = self._read_unlocked()
            document["connections"][provider] = record
            atomic_write_json(self.path, document, indent=2, ensure_ascii=False, encrypt=True)
        return self.get_status(provider)

    def get_connection(self, provider: str) -> Optional[Dict[str, Any]]:
        provider = str(provider or "").strip().lower()
        if provider not in _PROVIDER_SCOPES:
            raise ValueError("Provider must be google or microsoft")
        with self._guard():
            record = self._read_unlocked()["connections"].get(provider)
            return copy.deepcopy(record) if record else None

    def get_status(self, provider: str) -> Dict[str, Any]:
        connection = self.get_connection(provider)
        if connection is None:
            return {
                "provider": provider,
                "connected": False,
                "account_email": None,
                "scopes": [],
                "expires_at": None,
                "needs_refresh": False,
                "capabilities": [],
            }
        scopes = set(connection["scopes"])
        capabilities = [
            capability
            for capability, mapping in _CAPABILITY_SCOPES.items()
            if mapping[provider] in scopes
        ]
        expires_at = _parse_expiry(connection["expires_at"])
        return {
            "provider": provider,
            "connected": True,
            "account_email": connection["account_email"],
            "scopes": sorted(scopes),
            "expires_at": connection["expires_at"],
            "needs_refresh": expires_at <= datetime.now(timezone.utc),
            "capabilities": capabilities,
        }

    def list_status(self) -> list[Dict[str, Any]]:
        return [self.get_status(provider) for provider in sorted(_PROVIDER_SCOPES)]

    def connected_integrations(self) -> Set[str]:
        """Return only capabilities backed by an unexpired access token."""
        capabilities: Set[str] = set()
        for status in self.list_status():
            if status["connected"] and not status["needs_refresh"]:
                capabilities.update(status["capabilities"])
        return capabilities

    def disconnect(self, provider: str) -> bool:
        provider = str(provider or "").strip().lower()
        if provider not in _PROVIDER_SCOPES:
            raise ValueError("Provider must be google or microsoft")
        with self._guard():
            document = self._read_unlocked()
            existed = document["connections"].pop(provider, None) is not None
            if existed:
                atomic_write_json(self.path, document, indent=2, ensure_ascii=False, encrypt=True)
            return existed
