from __future__ import annotations

import os
import sys
import shutil
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
def test_python_runtime_and_staged_tool_contract_run_inside_appcontainer():
    kernel32, userenv, advapi32 = _windows_apis()
    profile_name = "VoiceAssistant.PythonProbe." + uuid.uuid4().hex
    sid = _create_profile(userenv, profile_name)
    sid_text = _sid_string(kernel32, advapi32, sid)

    root = Path(tempfile.gettempdir()) / ("va-python-appcontainer-" + uuid.uuid4().hex)
    staging = root / "staging"
    output_dir = root / "output"
    output = output_dir / "result.txt"
    root.mkdir()
    staging.mkdir()
    output_dir.mkdir()

    repo_root = Path(__file__).resolve().parents[1]
    tools_dir = staging / "tools"
    plugins_dir = staging / "plugins"
    tools_dir.mkdir()
    plugins_dir.mkdir()
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugins_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "base.py").write_text(
        (repo_root / "tools" / "base.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (plugins_dir / "probe_plugin.py").write_text(
        "from tools.base import BaseTool\n"
        "class ProbeTool(BaseTool):\n"
        "    @property\n"
        "    def name(self): return 'appcontainer_python_ok'\n"
        "    @property\n"
        "    def description(self): return 'probe'\n"
        "    @property\n"
        "    def parameters(self): return {'type': 'object', 'properties': {}}\n"
        "    def execute(self, **kwargs): return {'content': 'ok', 'is_error': False}\n",
        encoding="utf-8",
    )
    script = staging / "probe.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(staging)!r})\n"
        "from pathlib import Path\n"
        "from plugins.probe_plugin import ProbeTool\n"
        f"Path({str(output)!r}).write_text(ProbeTool().name, encoding='utf-8')\n",
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

        exit_code = run_appcontainer_command(
            Path(sys.executable),
            ["-I", str(script)],
            profile_name=profile_name,
            timeout_seconds=20,
            appcontainer_sid=sid,
        )
        assert exit_code == 0
        assert output.is_file()
        assert output.read_text(encoding="utf-8") == "appcontainer_python_ok"
    finally:
        for path in reversed(granted):
            _remove_acl(path, sid_text)
        _remove_acl(root, sid_text)
        userenv.DeleteAppContainerProfile(profile_name)
        advapi32.FreeSid(sid)
        shutil.rmtree(root, ignore_errors=True)
