from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

import agent.tool_registry as registry_module
from agent.plugin_marketplace import build_manifest_for_file, validate_manifest
from agent.policy import PolicyEngine
from tools.plugin_capability_inventory import inspect_source
from agent.tool_registry import PluginWorkerTool


def _manifest(entry: Path, sandbox: str = "appcontainer") -> dict:
    return {
        "name": entry.stem,
        "version": "1.0.0",
        "entry": entry.name,
        "tools": ["json_formatter"],
        "permissions": [],
        "checksum_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
        "execution_sandbox": sandbox,
    }


def test_manifest_rejects_unknown_or_non_string_execution_sandbox():
    valid = {
        "name": "sample",
        "version": "1.0.0",
        "entry": "sample.py",
        "tools": ["sample"],
        "permissions": [],
        "checksum_sha256": "0" * 64,
    }
    for value in ("unknown", [], {}, None):
        candidate = dict(valid, execution_sandbox=value)
        ok, errors = validate_manifest(candidate)
        assert ok is False
        assert any("execution_sandbox" in error for error in errors)



def test_manifest_builder_preserves_default_and_emits_explicit_sandbox(tmp_path):
    entry = tmp_path / "sample.py"
    entry.write_text("# sample plugin\n", encoding="utf-8")
    default_manifest = build_manifest_for_file(entry, tools=["sample"])
    assert "execution_sandbox" not in default_manifest

    appcontainer_manifest = build_manifest_for_file(
        entry, tools=["sample"], execution_sandbox="appcontainer"
    )
    assert appcontainer_manifest["execution_sandbox"] == "appcontainer"

    with pytest.raises(ValueError, match="execution_sandbox"):
        build_manifest_for_file(entry, tools=["sample"], execution_sandbox="unsafe")



def test_static_plugin_inventory_flags_builtin_dynamic_import(tmp_path):
    entry = tmp_path / "dynamic_import.py"
    entry.write_text("__import__('ctypes')", encoding="utf-8")
    inventory = inspect_source(entry, {"permissions": []})
    assert "dynamic_import" in inventory["warnings"]


def test_appcontainer_proxy_never_falls_back_to_same_user_worker(tmp_path, monkeypatch):
    class FailingAppContainerRunner:
        def __init__(self, policy):
            self.policy = policy

        def execute(self, **kwargs):
            return {"ok": False, "error_code": "appcontainer_worker_timeout"}

    def forbidden_fallback(*args, **kwargs):
        pytest.fail("AppContainer failure must never fall back to the same-user worker")

    monkeypatch.setattr(registry_module, "AppContainerPluginRunner", FailingAppContainerRunner)
    monkeypatch.setattr(registry_module, "_run_plugin_worker", forbidden_fallback)

    entry = tmp_path / "appcontainer_plugin.py"
    entry.write_text("# manifest-backed test source\n", encoding="utf-8")
    tool = PluginWorkerTool(
        entry=entry,
        sha256=hashlib.sha256(entry.read_bytes()).hexdigest(),
        class_name="SampleTool",
        metadata={"name": "appcontainer_sample"},
        permissions=[],
        sandbox_mode="appcontainer",
    )
    result = tool.execute()
    assert result["is_error"] is True
    assert result["error_code"] == "appcontainer_worker_timeout"


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer registry integration test")
def test_tool_registry_uses_appcontainer_for_discovery_and_every_invocation(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "plugins" / "json_formatter.py"
    entry = plugins / "json_formatter.py"
    shutil.copy2(source, entry)
    manifest = _manifest(entry)
    (plugins / "json_formatter.manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(registry_module, "PLUGINS_DIR", plugins)

    registry = registry_module.ToolRegistry()
    tool = registry.get("json_formatter")
    assert isinstance(tool, PluginWorkerTool)
    assert tool._sandbox_mode == "appcontainer"

    result = registry.execute(
        "json_formatter",
        {"json_string": '{"sandbox":"appcontainer","ok":true}'},
    )
    assert result["is_error"] is False
    assert result["content"] == '{\n  "sandbox": "appcontainer",\n  "ok": true\n}'

@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer capability-broker integration test")
def test_registry_appcontainer_plugin_uses_host_read_broker_after_policy_attachment(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target = allowed_root / "source.txt"
    target.write_text("read through the host broker", encoding="utf-8")

    entry = plugins / "broker_reader.py"
    entry.write_text(
        "from tools.base import BaseTool\n"
        "from tools.plugin_capability_client import read_text\n"
        "class BrokerReaderTool(BaseTool):\n"
        "    @property\n"
        "    def name(self): return 'broker_reader'\n"
        "    @property\n"
        "    def description(self): return 'Read through the host capability broker'\n"
        "    @property\n"
        "    def parameters(self): return {'type': 'object', 'properties': {'path': {'type': 'string'}}}\n"
        "    def execute(self, path): return {'content': read_text(path), 'is_error': False}\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "broker_reader",
        "version": "1.0.0",
        "entry": entry.name,
        "tools": ["broker_reader"],
        "permissions": ["filesystem:read"],
        "checksum_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
        "execution_sandbox": "appcontainer",
    }
    (plugins / "broker_reader.manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(registry_module, "PLUGINS_DIR", plugins)

    # Registry metadata discovery happens before the application attaches its
    # shared policy instance. Describe must not require or start a capability broker.
    registry = registry_module.ToolRegistry()
    tool = registry.get("broker_reader")
    assert isinstance(tool, PluginWorkerTool)
    assert tool._sandbox_mode == "appcontainer"

    policy = PolicyEngine(path=tmp_path / "policy.json")
    policy.data["allowed_read_roots"] = [str(allowed_root)]
    registry.set_policy(policy)

    result = registry.execute("broker_reader", {"path": str(target)})
    assert result["is_error"] is False
    assert result["content"] == "read through the host broker"




def test_example_hello_manifest_opts_in_appcontainer():
    repo_root = Path(__file__).resolve().parents[1]
    manifest_path = repo_root / "plugins" / "example_hello_tool.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    ok, errors = validate_manifest(manifest)
    assert ok is True, errors
    assert manifest["entry"] == "example_hello_tool.py"
    assert manifest["permissions"] == []
    assert manifest["execution_sandbox"] == "appcontainer"


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer example plugin integration test")
def test_tool_registry_executes_hello_example_inside_appcontainer(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "plugins" / "example_hello_tool.py"
    entry = plugins / "example_hello_tool.py"
    shutil.copy2(source, entry)
    manifest = {
        "name": "example_hello_tool",
        "version": "1.0.0",
        "entry": entry.name,
        "tools": ["say_hello"],
        "permissions": [],
        "checksum_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
        "execution_sandbox": "appcontainer",
    }
    (plugins / "example_hello_tool.manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(registry_module, "PLUGINS_DIR", plugins)

    registry = registry_module.ToolRegistry()
    tool = registry.get("say_hello")
    assert isinstance(tool, PluginWorkerTool)
    assert tool._sandbox_mode == "appcontainer"

    result = registry.execute("say_hello", {"name": "Fahad"})
    assert result["is_error"] is False
    assert result["content"] == "Hello Fahad! This message came from a custom plugin tool."
