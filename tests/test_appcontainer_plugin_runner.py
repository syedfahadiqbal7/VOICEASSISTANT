from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from agent.appcontainer_plugin_runner import AppContainerPluginRunner
from agent.policy import PolicyEngine


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer integration tests")


def _policy(tmp_path):
    return PolicyEngine(path=tmp_path / "policy.json")


def test_json_formatter_plugin_runs_inside_appcontainer(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    entry = repo_root / "plugins" / "json_formatter.py"
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    runner = AppContainerPluginRunner(_policy(tmp_path))

    response = runner.execute(
        entry,
        digest,
        "JSONFormatterTool",
        "json_formatter",
        {"json_string": '{"hello":"appcontainer","ok":true}'},
        permissions=[],
        timeout_seconds=20,
    )
    assert response["ok"] is True, f"type={response.get('error')} stage={response.get('stage')} resource={response.get('resource')} detail={response.get('detail')} winerror={response.get('winerror')} errno={response.get('errno')}"
    assert response["result"]["content"] == '{\n  "hello": "appcontainer",\n  "ok": true\n}'
    assert response["worker_exit_code"] == 0


def test_appcontainer_plugin_uses_named_pipe_host_broker_for_file_read(tmp_path):
    allowed_root = tmp_path / "Documents"
    allowed_root.mkdir()
    target = allowed_root / "readme.txt"
    target.write_text("read only through host broker", encoding="utf-8")
    policy = _policy(tmp_path)
    policy.data["allowed_read_roots"] = [str(allowed_root)]

    entry = tmp_path / "broker_reader.py"
    entry.write_text(
        "from tools.base import BaseTool\n"
        "from tools.plugin_capability_client import read_text\n"
        "class BrokerReaderTool(BaseTool):\n"
        "    @property\n"
        "    def name(self): return 'broker_reader'\n"
        "    @property\n"
        "    def description(self): return 'Read a file through the host broker'\n"
        "    @property\n"
        "    def parameters(self): return {'type': 'object', 'properties': {'path': {'type': 'string'}}}\n"
        "    def execute(self, path): return {'content': read_text(path), 'is_error': False}\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    runner = AppContainerPluginRunner(policy)
    response = runner.execute(
        entry,
        digest,
        "BrokerReaderTool",
        "broker_reader",
        {"path": str(target)},
        permissions=["filesystem:read"],
        timeout_seconds=20,
    )
    assert response["ok"] is True, f"worker_error={response.get('error')} resource={response.get('resource')} detail={response.get('detail')} code={response.get('error_code')}"
    assert response["result"]["content"] == "read only through host broker"
    assert response["worker_exit_code"] == 0


def test_appcontainer_runner_rejects_native_or_unsupported_plugin_imports(tmp_path):
    entry = tmp_path / "unsafe_plugin.py"
    entry.write_text("import ctypes\n", encoding="utf-8")
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    runner = AppContainerPluginRunner(_policy(tmp_path))
    response = runner.execute(
        entry, digest, "UnsafeTool", "unsafe_tool", {}, permissions=[],
    )
    assert response["ok"] is False
    assert response["error_code"] == "plugin_not_appcontainer_compatible"
