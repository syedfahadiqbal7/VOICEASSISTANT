"""Encrypted, revocable per-device credentials and roles for mobile clients.

Device secrets are returned only during enrollment. Only SHA-256 digests are
persisted, and the complete registry is encrypted at rest.
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
from mobile_api.authorization import DEFAULT_ROLE, is_valid_role

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

        # Pre-RBAC entries are least-privilege operators. Unknown/malformed roles
        # are corruption/security errors and must never be interpreted permissively.
        changed = False
        for device in value["devices"]:
            if not isinstance(device, dict):
                raise ValueError("Mobile device registry contains an invalid device entry")
            if "role" not in device:
                device["role"] = DEFAULT_ROLE
                changed = True
            elif not is_valid_role(device["role"]):
                raise ValueError("Mobile device registry contains an unsupported role")
        if changed:
            self._save(value)
        return value

    def _save(self, value: dict[str, Any]) -> None:
        atomic_write_json(self.path, value, encrypt=True)

    def register(self, name: str, role: str = DEFAULT_ROLE) -> dict[str, str]:
        label = str(name or "").strip()
        if not label or len(label) > 80 or any(ord(ch) < 32 for ch in label):
            raise ValueError("Device name must contain 1–80 printable characters")
        if not is_valid_role(role):
            raise ValueError("Unsupported device role")
        device_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        with _lock:
            data = self._load()
            data["devices"].append({
                "device_id": device_id,
                "name": label,
                "token_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                "role": role,
                "created_at": now,
                "revoked_at": None,
            })
            self._save(data)
        return {"device_id": device_id, "device_token": secret, "name": label, "role": role}

    def authenticate_principal(self, device_id: str, device_token: str) -> dict[str, str] | None:
        """Return a minimal authenticated principal; None means denied."""
        identifier = str(device_id or "").strip().lower()
        secret = str(device_token or "")
        if not _DEVICE_ID.fullmatch(identifier) or not secret or len(secret) > 256:
            return None
        supplied_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        with _lock:
            data = self._load()
            for device in data["devices"]:
                if device.get("device_id") != identifier:
                    continue
                expected = str(device.get("token_sha256") or "")
                matched = hmac.compare_digest(supplied_hash, expected)
                if not matched or device.get("revoked_at") is not None:
                    return None
                role = device.get("role")
                if not is_valid_role(role):
                    return None
                return {
                    "device_id": identifier,
                    "name": str(device.get("name", "")),
                    "role": role,
                }
        hmac.compare_digest(supplied_hash, "0" * 64)
        return None

    def authenticate(self, device_id: str, device_token: str) -> bool:
        return self.authenticate_principal(device_id, device_token) is not None

    def set_role(self, device_id: str, role: str) -> bool:
        identifier = str(device_id or "").strip().lower()
        if not _DEVICE_ID.fullmatch(identifier):
            raise ValueError("Invalid device ID")
        if not is_valid_role(role):
            raise ValueError("Unsupported device role")
        with _lock:
            data = self._load()
            target = next((item for item in data["devices"] if item["device_id"] == identifier), None)
            if target is None or target.get("revoked_at") is not None:
                return False
            if target.get("role") == role:
                return False
            if target.get("role") == "admin" and role != "admin":
                active_admins = [
                    item for item in data["devices"]
                    if item.get("role") == "admin" and item.get("revoked_at") is None
                ]
                if len(active_admins) <= 1:
                    raise ValueError("Cannot demote the last active admin device")
            target["role"] = role
            target["role_updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save(data)
            return True

    def revoke(self, device_id: str) -> bool:
        identifier = str(device_id or "").strip().lower()
        if not _DEVICE_ID.fullmatch(identifier):
            raise ValueError("Invalid device ID")
        with _lock:
            data = self._load()
            for device in data["devices"]:
                if device.get("device_id") == identifier:
                    if device.get("revoked_at") is not None:
                        return False
                    if device.get("role") == "admin":
                        active_admins = [
                            item for item in data["devices"]
                            if item.get("role") == "admin" and item.get("revoked_at") is None
                        ]
                        if len(active_admins) <= 1:
                            raise ValueError("Cannot revoke the last active admin device")
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
                "role": item["role"],
                "created_at": item.get("created_at"),
                "role_updated_at": item.get("role_updated_at"),
                "revoked_at": item.get("revoked_at"),
                "status": "revoked" if item.get("revoked_at") else "active",
            } for item in data["devices"]]


registry = MobileDeviceRegistry()
