"""Security regression tests for local encrypted recovery kits."""
from __future__ import annotations

import base64
import os

import pytest

from agent import keyring
from agent.key_recovery import (
    RecoveryKitError, export_recovery_kit, import_recovery_kit,
)


PASSPHRASE = "a much longer recovery phrase 2026"


def _prepare(tmp_path, monkeypatch):
    root = tmp_path / "install"
    history = root / "history"
    history.mkdir(parents=True)
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"R" * 32).decode("ascii"),
    )
    monkeypatch.setattr(keyring, "KEYRING_FILE", history / ".voiceassistant_keyring.v1")
    ring = keyring.create_initial_keyring(b"L" * 32, history / ".voiceassistant_keyring.v1", source="environment")
    return root, history, history / ".voiceassistant_keyring.v1", ring


def test_recovery_kit_round_trip_preserves_every_key_generation(tmp_path, monkeypatch):
    root, _history, ring_path, original = _prepare(tmp_path, monkeypatch)
    # Add a retained generation to prove the kit is not merely the active DEK.
    value = dict(original)
    value["keys"] = dict(original["keys"])
    old_active = value["active_key_id"]
    value["keys"][old_active] = dict(value["keys"][old_active], state="retained")
    import secrets
    new_id = secrets.token_hex(16)
    value["keys"][new_id] = {
        "key": base64.b64encode(b"N" * 32).decode("ascii"),
        "created_at": "2026-11-18T00:00:00+00:00",
        "state": "active",
    }
    value["active_key_id"] = new_id
    keyring.save_keyring(value, ring_path, source="environment")
    expected = keyring.load_keyring(ring_path, source="environment")

    kit = tmp_path / "offline" / "voiceassistant.varecovery"
    exported = export_recovery_kit(root, kit, PASSPHRASE)
    assert exported["ok"] is True
    assert exported["key_count"] == len(expected["keys"])
    assert kit.read_bytes().startswith(b"VARECOV")

    ring_path.unlink()
    imported = import_recovery_kit(root, kit, PASSPHRASE, confirm=True)
    assert imported["ok"] is True
    assert imported["key_count"] == len(expected["keys"])
    assert keyring.load_keyring(ring_path, source="environment") == expected


def test_export_requires_existing_keyring_and_never_overwrites_kit(tmp_path, monkeypatch):
    root, _history, ring_path, _ring = _prepare(tmp_path, monkeypatch)
    kit = tmp_path / "kit.varecovery"
    export_recovery_kit(root, kit, PASSPHRASE)
    original = kit.read_bytes()
    with pytest.raises(RecoveryKitError, match="refusing to overwrite"):
        export_recovery_kit(root, kit, PASSPHRASE)
    assert kit.read_bytes() == original


def test_wrong_passphrase_and_tampering_fail_closed(tmp_path, monkeypatch):
    root, _history, ring_path, _ring = _prepare(tmp_path, monkeypatch)
    kit = tmp_path / "kit.varecovery"
    export_recovery_kit(root, kit, PASSPHRASE)
    payload = kit.read_bytes()

    ring_path.unlink()
    with pytest.raises(RecoveryKitError, match="authentication failed"):
        import_recovery_kit(root, kit, "wrong recovery passphrase", confirm=True)
    assert not ring_path.exists()

    damaged = bytearray(payload)
    damaged[-1] ^= 0x01
    kit.write_bytes(damaged)
    with pytest.raises(RecoveryKitError, match="authentication failed"):
        import_recovery_kit(root, kit, PASSPHRASE, confirm=True)
    assert not ring_path.exists()


def test_truncated_unsupported_version_and_unsafe_kdf_are_rejected(tmp_path, monkeypatch):
    root, _history, ring_path, _ring = _prepare(tmp_path, monkeypatch)
    kit = tmp_path / "kit.varecovery"
    export_recovery_kit(root, kit, PASSPHRASE)
    original = kit.read_bytes()
    ring_path.unlink()

    kit.write_bytes(original[:12])
    with pytest.raises(RecoveryKitError):
        import_recovery_kit(root, kit, PASSPHRASE, confirm=True)

    unsupported = bytearray(original)
    unsupported[7] = 99
    kit.write_bytes(unsupported)
    with pytest.raises(RecoveryKitError, match="Unsupported"):
        import_recovery_kit(root, kit, PASSPHRASE, confirm=True)

    unsafe_kdf = bytearray(original)
    unsafe_kdf[8:12] = (1).to_bytes(4, "big")
    kit.write_bytes(unsafe_kdf)
    with pytest.raises(RecoveryKitError, match="KDF"):
        import_recovery_kit(root, kit, PASSPHRASE, confirm=True)
    assert not ring_path.exists()


def test_import_requires_confirmation_and_refuses_to_overwrite_existing_keyring(tmp_path, monkeypatch):
    root, _history, ring_path, original = _prepare(tmp_path, monkeypatch)
    kit = tmp_path / "kit.varecovery"
    export_recovery_kit(root, kit, PASSPHRASE)
    with pytest.raises(PermissionError):
        import_recovery_kit(root, kit, PASSPHRASE)
    with pytest.raises(RecoveryKitError, match="already exists"):
        import_recovery_kit(root, kit, PASSPHRASE, confirm=True)
    assert keyring.load_keyring(ring_path, source="environment") == original


def test_import_requires_regular_file_and_rejects_symlink(tmp_path, monkeypatch):
    root, _history, ring_path, _ring = _prepare(tmp_path, monkeypatch)
    kit = tmp_path / "kit.varecovery"
    export_recovery_kit(root, kit, PASSPHRASE)
    ring_path.unlink()
    link = tmp_path / "linked.varecovery"
    try:
        link.symlink_to(kit)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(RecoveryKitError, match="symbolic-link"):
        import_recovery_kit(root, link, PASSPHRASE, confirm=True)
    assert not ring_path.exists()
