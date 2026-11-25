from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from security.windows_appcontainer import _remove_acl, _workspace_exceeds_limits
from tools.plugin_capability_inventory import inspect_source


def test_workspace_usage_accepts_only_bounded_flat_ipc_files(tmp_path):
    (tmp_path / "request.json").write_bytes(b"r" * 100)
    (tmp_path / "response.json").write_bytes(b"")
    (tmp_path / "response.json.tmp").write_bytes(b"")
    assert _workspace_exceeds_limits(
        tmp_path,
        max_bytes=1024,
        max_files=3,
        max_file_bytes=512,
    ) is False


def test_workspace_usage_rejects_oversized_individual_file(tmp_path):
    (tmp_path / "response.json").write_bytes(b"x" * 513)
    assert _workspace_exceeds_limits(
        tmp_path,
        max_bytes=4096,
        max_files=3,
        max_file_bytes=512,
    ) is True


def test_workspace_usage_rejects_aggregate_size_exhaustion(tmp_path):
    (tmp_path / "request.json").write_bytes(b"x" * 300)
    (tmp_path / "response.json").write_bytes(b"y" * 300)
    assert _workspace_exceeds_limits(
        tmp_path,
        max_bytes=512,
        max_files=3,
        max_file_bytes=400,
    ) is True


def test_workspace_usage_rejects_extra_files_and_nested_directories(tmp_path):
    for name in ("one", "two", "three", "four"):
        (tmp_path / f"{name}.tmp").write_bytes(b"x")
    assert _workspace_exceeds_limits(
        tmp_path,
        max_bytes=4096,
        max_files=3,
        max_file_bytes=512,
    ) is True

    nested = tmp_path / "nested"
    nested.mkdir()
    assert _workspace_exceeds_limits(
        tmp_path,
        max_bytes=4096,
        max_files=10,
        max_file_bytes=512,
    ) is True


def test_workspace_usage_fails_closed_when_directory_is_missing(tmp_path):
    assert _workspace_exceeds_limits(
        tmp_path / "missing",
        max_bytes=1024,
        max_files=3,
        max_file_bytes=512,
    ) is True


def test_acl_cleanup_timeout_is_reported_without_raising(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 10))

    monkeypatch.setattr(subprocess, "run", timeout)
    assert _remove_acl(target, "S-1-15-2-123") is False


def test_acl_cleanup_nonzero_exit_is_reported_without_raising(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()

    class Result:
        returncode = 5

    calls = []

    def failed(*args, **kwargs):
        calls.append(args[0])
        return Result()

    monkeypatch.setattr(subprocess, "run", failed)
    assert _remove_acl(target, "S-1-15-2-123") is False
    assert len(calls) == 2


def test_acl_cleanup_of_missing_path_is_idempotent(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("icacls should not run for a path that is already gone")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert _remove_acl(tmp_path / "missing", "S-1-15-2-123") is True


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("open('input.txt', 'r').read()", {"filesystem:read"}),
        ("open('output.txt', 'w').write('x')", {"filesystem:write"}),
        ("open('output.txt', 'a').write('x')", {"filesystem:write"}),
        ("open('file.txt', 'r+').write('x')", {"filesystem:read", "filesystem:write"}),
        ("open('file.txt', mode).read()", {"filesystem:read", "filesystem:write"}),
        ("open('input.txt').read()", {"filesystem:read"}),
    ],
)
def test_static_inventory_classifies_open_mode_capabilities(tmp_path, source, expected):
    entry = tmp_path / "plugin.py"
    entry.write_text(source, encoding="utf-8")

    inventory = inspect_source(entry, {"permissions": sorted(expected)})

    assert set(inventory["observed_capabilities"]) == expected
    assert inventory["undeclared_observed"] == []
