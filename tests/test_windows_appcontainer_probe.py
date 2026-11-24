from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from security.windows_appcontainer import (
    _create_profile,
    _grant_acl,
    _remove_acl,
    _sid_string,
    _windows_apis,
    run_appcontainer_command,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer integration test")
def test_appcontainer_cannot_read_host_file_without_explicit_package_acl():
    kernel32, userenv, advapi32 = _windows_apis()
    profile_name = "VoiceAssistant.Probe." + uuid.uuid4().hex
    sid = _create_profile(userenv, profile_name)
    sid_text = _sid_string(kernel32, advapi32, sid)

    root = Path(tempfile.gettempdir()) / ("va-appcontainer-" + uuid.uuid4().hex)
    shared = root / "shared"
    root.mkdir()
    shared.mkdir()
    secret = root / "host-secret.txt"
    output = shared / "probe-output.txt"
    secret.write_text("THIS_MUST_NOT_BE_READ", encoding="utf-8")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"

    try:
        # AppContainer can traverse the temporary parent but has no inherited
        # read permission to the sentinel file. It receives access only to the
        # dedicated output directory.
        _grant_acl(root, sid_text, "(X)")
        _grant_acl(shared, sid_text, "(OI)(CI)M")
        command = f'type "{secret}" > "{output}" 2>&1'
        exit_code = run_appcontainer_command(
            executable,
            ["/d", "/c", command],
            profile_name=profile_name,
            timeout_seconds=15,
            appcontainer_sid=sid,
        )
        assert exit_code != 0
        assert output.is_file()
        observed = output.read_text(encoding="utf-8", errors="replace")
        assert "THIS_MUST_NOT_BE_READ" not in observed
        assert "denied" in observed.lower() or "access" in observed.lower()
    finally:
        _remove_acl(shared, sid_text)
        _remove_acl(root, sid_text)
        userenv.DeleteAppContainerProfile(profile_name)
        kernel32.FreeSid(sid)
        shutil.rmtree(root, ignore_errors=True)
