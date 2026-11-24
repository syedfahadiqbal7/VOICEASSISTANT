from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from security.windows_named_pipe import WindowsNamedPipeServer, send_named_pipe_message


@pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe integration test")
def test_named_pipe_server_auth_acl_allows_host_owner():
    pipe_name = r"\\.\pipe\VoiceAssistantHostPipe-" + uuid.uuid4().hex
    server = WindowsNamedPipeServer(
        pipe_name,
        "S-1-15-2-1",
        lambda request: b'{"ok":true,"echo":"host"}\n',
    )
    # The test process is the pipe owner; the package SID is only an additional
    # allowed principal for the AppContainer test below.
    server.start()
    try:
        response = send_named_pipe_message(pipe_name, b'{"ping":"host"}\n')
        assert b'"echo":"host"' in response
    finally:
        server.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer named-pipe test")
def test_appcontainer_can_reach_only_sid_acl_named_pipe(tmp_path):
    from security.windows_appcontainer import (
        _create_profile, _grant_acl, _remove_acl, _sid_string,
        _windows_apis, run_appcontainer_command,
    )

    kernel32, userenv, advapi32 = _windows_apis()
    profile_name = "VoiceAssistant.PipeProbe." + uuid.uuid4().hex
    sid = _create_profile(userenv, profile_name)
    sid_text = _sid_string(kernel32, advapi32, sid)
    pipe_name = r"\\.\pipe\VoiceAssistantAppContainer-" + uuid.uuid4().hex
    server = WindowsNamedPipeServer(
        pipe_name,
        sid_text,
        lambda request: b'{"ok":true,"source":"host-broker"}\n',
    )

    root = Path(tempfile.gettempdir()) / ("va-pipe-appcontainer-" + uuid.uuid4().hex)
    staging = root / "staging"
    output_dir = root / "output"
    output = output_dir / "pipe-result.json"
    root.mkdir()
    staging.mkdir()
    output_dir.mkdir()
    security_dir = staging / "security"
    security_dir.mkdir()
    (security_dir / "__init__.py").write_text("", encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]
    (security_dir / "windows_named_pipe.py").write_text(
        (repo_root / "security" / "windows_named_pipe.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script = staging / "pipe_probe.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(staging)!r})\n"
        "from pathlib import Path\n"
        "from security.windows_named_pipe import send_named_pipe_message\n"
        f"reply = send_named_pipe_message({pipe_name!r}, b'{{\"ping\":\"appcontainer\"}}\\n')\n"
        f"Path({str(output)!r}).write_bytes(reply)\n",
        encoding="utf-8",
    )

    runtime_roots = sorted({Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()}, key=str)
    granted = []
    try:
        _grant_acl(root, sid_text, "(X)")
        for runtime_root in runtime_roots:
            _grant_acl(runtime_root, sid_text, "(OI)(CI)RX")
            granted.append(runtime_root)
        _grant_acl(staging, sid_text, "(OI)(CI)RX")
        granted.append(staging)
        _grant_acl(output_dir, sid_text, "(OI)(CI)M")
        granted.append(output_dir)
        server.start()

        exit_code = run_appcontainer_command(
            Path(sys.executable), ["-I", str(script)],
            profile_name=profile_name, timeout_seconds=20, appcontainer_sid=sid,
        )
        assert exit_code == 0
        assert output.is_file()
        assert b'"source":"host-broker"' in output.read_bytes()
    finally:
        server.close()
        for path in reversed(granted):
            _remove_acl(path, sid_text)
        _remove_acl(root, sid_text)
        userenv.DeleteAppContainerProfile(profile_name)
        advapi32.FreeSid(sid)
        shutil.rmtree(root, ignore_errors=True)
