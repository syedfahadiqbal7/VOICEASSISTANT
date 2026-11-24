from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from agent.plugin_capability_broker import PluginCapabilityBroker
from agent.policy import PolicyEngine
from tools.plugin_capability_client import PluginCapabilityError, read_bytes, read_text


def _policy(tmp_path):
    return PolicyEngine(path=tmp_path / "policy.json")


def test_broker_reads_text_only_under_policy_approved_root(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("hello broker", encoding="utf-8")
    policy = _policy(tmp_path)
    policy.data["allowed_read_roots"] = [str(root)]
    broker = PluginCapabilityBroker(policy, "read_document", ["filesystem:read"])
    config = broker.start()
    monkeypatch.setenv("VOICEASSISTANT_BROKER_HOST", config["host"])
    monkeypatch.setenv("VOICEASSISTANT_BROKER_PORT", str(config["port"]))
    monkeypatch.setenv("VOICEASSISTANT_BROKER_TOKEN", config["token"])
    try:
        assert read_text(str(target)) == "hello broker"
    finally:
        broker.close()


def test_broker_denies_path_outside_configured_root(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    policy = _policy(tmp_path)
    policy.data["allowed_read_roots"] = [str(root)]
    broker = PluginCapabilityBroker(policy, "read_document", ["filesystem:read"])
    config = broker.start()
    monkeypatch.setenv("VOICEASSISTANT_BROKER_HOST", config["host"])
    monkeypatch.setenv("VOICEASSISTANT_BROKER_PORT", str(config["port"]))
    monkeypatch.setenv("VOICEASSISTANT_BROKER_TOKEN", config["token"])
    try:
        with pytest.raises(PluginCapabilityError, match="capability_denied"):
            read_text(str(outside))
    finally:
        broker.close()


def test_broker_denies_undeclared_capability_and_invalid_token(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("hello", encoding="utf-8")
    policy = _policy(tmp_path)
    policy.data["allowed_read_roots"] = [str(root)]
    broker = PluginCapabilityBroker(policy, "read_document", [])
    config = broker.start()
    monkeypatch.setenv("VOICEASSISTANT_BROKER_HOST", config["host"])
    monkeypatch.setenv("VOICEASSISTANT_BROKER_PORT", str(config["port"]))
    monkeypatch.setenv("VOICEASSISTANT_BROKER_TOKEN", config["token"])
    try:
        with pytest.raises(PluginCapabilityError, match="capability_denied"):
            read_text(str(target))
        monkeypatch.setenv("VOICEASSISTANT_BROKER_TOKEN", "wrong-token")
        with pytest.raises(PluginCapabilityError, match="unauthorized"):
            read_text(str(target))
    finally:
        broker.close()


def test_broker_bytes_operation_returns_exact_bytes(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    target = root / "binary.bin"
    target.write_bytes(bytes([0, 1, 2, 255]))
    policy = _policy(tmp_path)
    policy.data["allowed_read_roots"] = [str(root)]
    broker = PluginCapabilityBroker(policy, "read_document", ["filesystem:read"])
    config = broker.start()
    monkeypatch.setenv("VOICEASSISTANT_BROKER_HOST", config["host"])
    monkeypatch.setenv("VOICEASSISTANT_BROKER_PORT", str(config["port"]))
    monkeypatch.setenv("VOICEASSISTANT_BROKER_TOKEN", config["token"])
    try:
        assert read_bytes(str(target)) == bytes([0, 1, 2, 255])
    finally:
        broker.close()


def test_worker_plugin_can_call_host_broker_over_per_invocation_ipc(tmp_path):
    from agent.tool_registry import _run_plugin_worker

    root = tmp_path / "Documents"
    root.mkdir()
    target = root / "worker.txt"
    target.write_text("read through host broker", encoding="utf-8")
    source = tmp_path / "broker_plugin.py"
    source.write_text(
        "from tools.base import BaseTool\n"
        "from tools.plugin_capability_client import read_text\n"
        "class BrokerReadTool(BaseTool):\n"
        "    @property\n"
        "    def name(self): return 'broker_read'\n"
        "    @property\n"
        "    def description(self): return 'Reads via the host broker'\n"
        "    @property\n"
        "    def parameters(self): return {'type': 'object', 'properties': {'path': {'type': 'string'}}}\n"
        "    def execute(self, path): return {'content': read_text(path), 'is_error': False}\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    policy = _policy(tmp_path)
    policy.data["allowed_read_roots"] = [str(root)]
    broker = PluginCapabilityBroker(policy, "broker_read", ["filesystem:read"])
    response = _run_plugin_worker({
        "protocol_version": 1,
        "mode": "execute",
        "entry": str(source),
        "sha256": digest,
        "class_name": "BrokerReadTool",
        "tool_name": "broker_read",
        "arguments": {"path": str(target)},
    }, broker=broker)
    assert response["ok"] is True
    assert response["result"]["content"] == "read through host broker"
