"""Encrypted, revocable per-device credentials for the mobile companion.

A device secret is generated once and shown only to the operator. Only its
SHA-256 digest is persisted; the registry itself is encrypted at rest.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.storage import atomic_write_json, load_json
from config import HISTORY_DIR

_DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.RLock()


class MobileDeviceRegistry:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else Path(HISTORY_DIR) / "mobile_devices.json"

    def is_configured(self) -> bool:
        """A present registry is authoritative, even when all devices are revoked."""
        return self.path.exists()

    def _load(self) -> dict[str, Any]:
        value = load_json(self.path, default={"version": 1, "devices": []}, encrypt=True)
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("devices"), list):
            raise ValueError("Mobile device registry has an unsupported or invalid shape")
        return value

    def _save(self, value: dict[str, Any]) -> None:
        atomic_write_json(self.path, value, encrypt=True)

    def register(self, name: str) -> dict[str, str]:
        label = str(name or "").strip()
        if not label or len(label) > 80 or any(ord(ch) < 32 for ch in label):
            raise ValueError("Device name must contain 1–80 printable characters")
        device_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with _lock:
            data = self._load()
            data["devices"].append({
                "device_id": device_id,
                "name": label,
                "token_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                "created_at": now,
                "revoked_at": None,
            })
            self._save(data)
        # The raw secret is returned only at enrollment time and is never persisted.
        return {"device_id": device_id, "device_token": secret, "name": label}

    def authenticate(self, device_id: str, device_token: str) -> bool:
        identifier = str(device_id or "").strip().lower()
        secret = str(device_token or "")
        if not _DEVICE_ID.fullmatch(identifier) or not secret or len(secret) > 256:
            return False
        supplied_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        with _lock:
            data = self._load()
            for device in data["devices"]:
                if not isinstance(device, dict) or device.get("device_id") != identifier:
                    continue
                expected = str(device.get("token_sha256") or "")
                matched = hmac.compare_digest(supplied_hash, expected)
                return matched and device.get("revoked_at") is None
        # Do a constant-time comparison on misses to reduce obvious timing differences.
        hmac.compare_digest(supplied_hash, "0" * 64)
        return False

    def revoke(self, device_id: str) -> bool:
        identifier = str(device_id or "").strip().lower()
        if not _DEVICE_ID.fullmatch(identifier):
            raise ValueError("Invalid device ID")
        with _lock:
            data = self._load()
            for device in data["devices"]:
                if isinstance(device, dict) and device.get("device_id") == identifier:
                    if device.get("revoked_at") is not None:
                        return False
                    device["revoked_at"] = datetime.now(timezone.utc).isoformat()
                    self._save(data)
                    return True
        return False

    def list_devices(self) -> list[dict[str, Any]]:
        with _lock:
            data = self._load()
            return [{
                "device_id": str(item.get("device_id", "")),
                "name": str(item.get("name", "")),
                "created_at": item.get("created_at"),
                "revoked_at": item.get("revoked_at"),
                "status": "revoked" if item.get("revoked_at") else "active",
            } for item in data["devices"] if isinstance(item, dict)]


registry = MobileDeviceRegistry()
