"""Explicit desktop identity CLI confirmation tests."""
from __future__ import annotations

from agent.remote_identity import RemoteIdentityStore
import remote_identity_cli


def test_cli_bootstrap_requires_exact_confirmation_and_never_prints_private_key(
    tmp_path, monkeypatch, capsys
):
    store = RemoteIdentityStore(tmp_path / "desktop_remote_identity.json")
    monkeypatch.setattr(remote_identity_cli, "RemoteIdentityStore", lambda: store)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    assert remote_identity_cli.main(["bootstrap"]) == 1
    assert store.status()["initialized"] is False

    monkeypatch.setattr("builtins.input", lambda _prompt: "CREATE DESKTOP IDENTITY")
    assert remote_identity_cli.main(["bootstrap"]) == 0
    output = capsys.readouterr().out
    assert "fingerprint_sha256" in output
    assert "public_key" in output
    assert "private_key" not in output
    assert store.status()["initialized"] is True


def test_cli_public_command_requires_initialized_identity(tmp_path, monkeypatch, capsys):
    store = RemoteIdentityStore(tmp_path / "desktop_remote_identity.json")
    monkeypatch.setattr(remote_identity_cli, "RemoteIdentityStore", lambda: store)

    assert remote_identity_cli.main(["public"]) == 1
    assert "not initialized" in capsys.readouterr().out
