from __future__ import annotations

import os
import socket
import threading
import uuid
from pathlib import Path

import pytest

from security.windows_appcontainer import _create_profile, _windows_apis, run_appcontainer_command


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer integration test")
def test_appcontainer_without_network_capabilities_cannot_reach_loopback_listener():
    kernel32, userenv, advapi32 = _windows_apis()
    profile_name = "VoiceAssistant.NetworkProbe." + uuid.uuid4().hex
    sid = _create_profile(userenv, profile_name)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    connected = threading.Event()

    def accept_one():
        try:
            connection, _ = listener.accept()
            connected.set()
            connection.close()
        except OSError:
            pass

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    try:
        assert curl.is_file(), "Windows CI image must provide System32 curl.exe"
        exit_code = run_appcontainer_command(
            curl,
            ["--max-time", "2", "--silent", "--output", "NUL",
             f"http://127.0.0.1:{listener.getsockname()[1]}/"],
            profile_name=profile_name,
            timeout_seconds=8,
            appcontainer_sid=sid,
        )
        assert exit_code != 0
        assert not connected.is_set()
    finally:
        listener.close()
        thread.join(timeout=4)
        userenv.DeleteAppContainerProfile(profile_name)
        advapi32.FreeSid(sid)
