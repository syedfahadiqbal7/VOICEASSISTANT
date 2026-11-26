"""Desktop remote identity provisioning, integrity and recovery regressions."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.data_protection import is_encrypted_bytes
from agent.remote_identity import RemoteIdentityError, RemoteIdentityStore
from agent.storage import atomic_write_json


def _store(path):
    return RemoteIdentityStore(path)


def test_status_does_not_create_or_replace_identity(tmp_path):
    path = tmp_path / "desktop_remote_identity.json"
    store = _store(path)

    status = store.status()

    assert status == {"initialized": False, "algorithm": "Ed25519"}
    assert not path.exists()


def test_bootstrap_requires_explicit_confirmation(tmp_path):
    store = _store(tmp_path / "desktop_remote_identity.json")

    with pytest.raises(PermissionError, match="Explicit owner confirmation"):
        store.bootstrap()

    assert store.status()["initialized"] is False


def test_bootstrap_writes_authenticated_encrypted_identity_and_public_only_status(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"I" * 32).decode("ascii"),
    )
    path = tmp_path / "desktop_remote_identity.json"
    store = _store(path)

    created = store.bootstrap(confirmed=True)
    status = store.status()

    assert is_encrypted_bytes(path.read_bytes())
    assert created["identity_id"] == status["identity_id"]
    assert created["fingerprint_sha256"] == status["fingerprint_sha256"]
    assert created["algorithm"] == "Ed25519"
    assert "private_key" not in created
    assert "private_key" not in status
    assert len(status["fingerprint_sha256"]) == 64
    assert len(status["public_key"]) == 43


def test_sign_and_verify_round_trip_and_reject_modified_payload(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"S" * 32).decode("ascii"),
    )
    store = _store(tmp_path / "desktop_remote_identity.json")
    public = store.bootstrap(confirmed=True)
    message = b"voiceassistant remote session descriptor v1"
    signature = store.sign(message)

    assert store.verify(public["public_key"], message, signature)
    assert not store.verify(public["public_key"], message + b"!", signature)
    assert not store.verify(public["public_key"], message, signature[:-1])


def test_bootstrap_never_overwrites_or_silently_rotates_existing_identity(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"R" * 32).decode("ascii"),
    )
    store = _store(tmp_path / "desktop_remote_identity.json")
    first = store.bootstrap(confirmed=True)

    with pytest.raises(RemoteIdentityError, match="already exists"):
        store.bootstrap(confirmed=True)

    assert store.status()["fingerprint_sha256"] == first["fingerprint_sha256"]


def test_corrupted_ciphertext_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"C" * 32).decode("ascii"),
    )
    path = tmp_path / "desktop_remote_identity.json"
    store = _store(path)
    store.bootstrap(confirmed=True)
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 0x01
    path.write_bytes(payload)

    with pytest.raises(RemoteIdentityError, match="authenticated or read"):
        store.status()


def test_plaintext_identity_is_rejected_without_automatic_migration(tmp_path):
    path = tmp_path / "desktop_remote_identity.json"
    atomic_write_json(path, {"schema_version": 1, "algorithm": "Ed25519"}, encrypt=False)

    with pytest.raises(RemoteIdentityError, match="not authenticated-encrypted"):
        _store(path).status()


def test_mismatched_public_and_private_keys_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"M" * 32).decode("ascii"),
    )
    store = _store(tmp_path / "desktop_remote_identity.json")
    created = store.bootstrap(confirmed=True)
    from agent.remote_identity import _b64encode
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    different = Ed25519PrivateKey.generate()
    private_raw = different.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    record = {
        "schema_version": 1,
        "identity_id": created["identity_id"],
        "algorithm": "Ed25519",
        "created_at": created["created_at"],
        "public_key": created["public_key"],
        "private_key": _b64encode(private_raw),
        "fingerprint_sha256": created["fingerprint_sha256"],
    }
    atomic_write_json(store.path, record, encrypt=True)

    with pytest.raises(RemoteIdentityError, match="key material failed validation"):
        store.status()


def test_concurrent_bootstrap_across_store_instances_creates_only_one_identity(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"T" * 32).decode("ascii"),
    )
    path = tmp_path / "desktop_remote_identity.json"

    def attempt():
        try:
            return _store(path).bootstrap(confirmed=True)["identity_id"]
        except RemoteIdentityError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: attempt(), range(2)))

    assert sum(value is not None for value in results) == 1
    assert _store(path).status()["initialized"] is True


def test_symlinked_identity_path_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICEASSISTANT_DATA_KEY",
        base64.urlsafe_b64encode(b"L" * 32).decode("ascii"),
    )
    target = tmp_path / "real.json"
    _store(target).bootstrap(confirmed=True)
    link = tmp_path / "desktop_remote_identity.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")

    with pytest.raises(Exception, match="symbolic links"):
        _store(link).status()


def test_rfc8032_ed25519_empty_message_vector_matches_mobile_client():
    public_raw = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    public_key = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")

    assert RemoteIdentityStore.verify(public_key, b"", signature)
    assert not RemoteIdentityStore.verify(public_key, b"tampered", signature)
