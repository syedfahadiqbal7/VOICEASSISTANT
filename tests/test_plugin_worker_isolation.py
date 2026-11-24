"""Regression tests for plugin subprocess crash containment."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agent.tool_registry import _run_plugin_worker


def _write_plugin(root: Path, source: str, tool_name: str = "worker_echo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    entry = root / "isolated_plugin.py"
    entry.write_text(source, encoding="utf-8")
    manifest = {
        "name": "isolated_plugin",
        "version": "1.0.0",
        "entry": entry.name,
        "tools": [tool_name],
        "permissions": [],
        "checksum_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
    }
    entry.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return entry


def _source(marker: Path) -> str:
    return f'''
import os
from pathlib import Path
from tools.base import BaseTool

Path({str(marker)!r}).write_text(str(os.getpid()), encoding="utf-8")

class WorkerEchoTool(BaseTool):
    @property
    def name(self): return "worker_echo"
    @property
    def description(self): return "Returns the worker process identifier."
    @property
    def parameters(self):
        return {{"type": "object", "properties": {{}}}}
    def execute(self, **kwargs):
        return {{"content": str(os.getpid()), "is_error": False}}
'''


def test_plugin_import_and_execution_run_outside_desktop_process(tmp_path, monkeypatch):
    import agent.tool_registry as registry_module

    plugins = tmp_path / "plugins"
    marker = tmp_path / "plugin_import_pid.txt"
    _write_plugin(plugins, _source(marker))
    monkeypatch.setattr(registry_module, "PLUGINS_DIR", plugins)

    registry = registry_module.ToolRegistry()
    tool = registry.get("worker_echo")
    assert tool is not None
    assert marker.is_file()
    import_pid = int(marker.read_text(encoding="utf-8"))
    assert import_pid != os.getpid()

    result = registry.execute("worker_echo", {})
    assert result["is_error"] is False
    assert int(result["content"]) != os.getpid()


def test_worker_rechecks_plugin_digest_before_import(tmp_path):
    import agent.tool_registry as registry_module

    plugins = tmp_path / "plugins"
    marker = tmp_path / "must_not_be_created.txt"
    entry = _write_plugin(plugins, _source(marker))
    original_digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    entry.write_text("# tampered after host verification\n", encoding="utf-8")

    response = _run_plugin_worker({
        "protocol_version": 1,
        "mode": "describe",
        "entry": str(entry),
        "sha256": original_digest,
    })
    assert response["ok"] is False
    assert response["error_code"] == "plugin_worker_failed"
    assert not marker.exists()


def test_worker_timeout_terminates_hung_plugin(tmp_path):
    marker = tmp_path / "not_used.txt"
    source = _source(marker).replace(
        'return {"content": str(os.getpid()), "is_error": False}',
        'import time; time.sleep(5); return {"content": "late", "is_error": False}',
    )
    entry = _write_plugin(tmp_path / "plugins", source)
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    response = _run_plugin_worker({
        "protocol_version": 1,
        "mode": "execute",
        "entry": str(entry),
        "sha256": digest,
        "class_name": "WorkerEchoTool",
        "tool_name": "worker_echo",
        "arguments": {},
    }, timeout=1)
    assert response["ok"] is False
    assert response["error_code"] == "worker_timeout"


def test_worker_rejects_symlinked_plugin_source(tmp_path):
    target = tmp_path / "real_plugin.py"
    target.write_text("# not a plugin", encoding="utf-8")
    link = tmp_path / "linked_plugin.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlink creation is not available for this test identity")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    response = _run_plugin_worker({
        "protocol_version": 1,
        "mode": "describe",
        "entry": str(link),
        "sha256": digest,
    })
    assert response["ok"] is False
    assert response["error_code"] == "plugin_worker_failed"


def test_worker_timeout_terminates_spawned_child_process_tree(tmp_path):
    import time
    import psutil

    marker = tmp_path / "spawned_child_pid.txt"
    source = _source(marker).replace(
        "import os\\n",
        "import os\\nimport subprocess\\nimport sys\\nimport time\\n",
    ).replace(
        'return {"content": str(os.getpid()), "is_error": False}',
        f'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"]); Path({str(marker)!r}).write_text(str(child.pid), encoding="utf-8"); time.sleep(20); return {{"content": "late", "is_error": False}}',
    )
    entry = _write_plugin(tmp_path / "plugins", source)
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    response = _run_plugin_worker({
        "protocol_version": 1,
        "mode": "execute",
        "entry": str(entry),
        "sha256": digest,
        "class_name": "WorkerEchoTool",
        "tool_name": "worker_echo",
        "arguments": {},
    }, timeout=2)
    assert response["error_code"] == "worker_timeout"
    assert marker.is_file()
    child_pid = int(marker.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and psutil.pid_exists(child_pid):
        try:
            if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
