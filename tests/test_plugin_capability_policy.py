from __future__ import annotations

from agent.policy import PolicyEngine


def _policy(tmp_path):
    return PolicyEngine(path=tmp_path / "policy.json")


def test_capability_request_requires_manifest_declaration(tmp_path):
    policy = _policy(tmp_path)
    decision = policy.evaluate_capability_request(
        "file_reader",
        "filesystem:read",
        "filesystem.read_text",
        {"path": str(tmp_path / "report.txt")},
        declared_permissions=[],
    )
    assert decision["allowed"] is False
    assert "not declared" in decision["reason"]


def test_capability_request_denies_generic_admin_and_unimplemented_network(tmp_path):
    policy = _policy(tmp_path)
    admin = policy.evaluate_capability_request(
        "admin_plugin", "system:admin", "system.run", {}, ["system:admin"], approved=True
    )
    network = policy.evaluate_capability_request(
        "web_plugin", "network:read", "network.http_get",
        {"url": "https://example.invalid"}, ["network:read"], approved=True
    )
    assert admin["allowed"] is False
    assert network["allowed"] is False
    assert "No broker handler" in network["reason"]


def test_filesystem_read_is_confined_to_configured_read_roots(tmp_path):
    policy = _policy(tmp_path)
    allowed_root = tmp_path / "Documents"
    allowed_root.mkdir()
    allowed_file = allowed_root / "report.txt"
    allowed_file.write_text("safe", encoding="utf-8")
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    policy.data["allowed_read_roots"] = [str(allowed_root)]

    allowed = policy.evaluate_capability_request(
        "read_document", "filesystem:read", "filesystem.read_text",
        {"path": str(allowed_file)}, ["filesystem:read"],
    )
    denied = policy.evaluate_capability_request(
        "read_document", "filesystem:read", "filesystem.read_text",
        {"path": str(outside_file)}, ["filesystem:read"],
    )
    assert allowed["allowed"] is True
    assert denied["allowed"] is False


def test_filesystem_write_requires_approval_and_allowed_root(tmp_path):
    policy = _policy(tmp_path)
    allowed_root = tmp_path / "Documents"
    allowed_root.mkdir()
    policy.data["allowed_write_roots"] = [str(allowed_root)]
    target = allowed_root / "report.txt"

    pending = policy.evaluate_capability_request(
        "document_writer", "filesystem:write", "filesystem.write_text",
        {"path": str(target)}, ["filesystem:write"],
    )
    approved = policy.evaluate_capability_request(
        "document_writer", "filesystem:write", "filesystem.write_text",
        {"path": str(target)}, ["filesystem:write"], approved=True,
    )
    outside = policy.evaluate_capability_request(
        "document_writer", "filesystem:write", "filesystem.write_text",
        {"path": str(tmp_path / "outside.txt")}, ["filesystem:write"], approved=True,
    )
    assert pending["allowed"] is False
    assert pending["require_gate"] is True
    assert approved["allowed"] is True
    assert outside["allowed"] is False


def test_read_only_role_blocks_mutating_capability_even_when_approved(tmp_path):
    policy = _policy(tmp_path)
    allowed_root = tmp_path / "Documents"
    allowed_root.mkdir()
    policy.data["allowed_write_roots"] = [str(allowed_root)]
    policy.data["role"] = "read_only"
    decision = policy.evaluate_capability_request(
        "document_writer", "filesystem:write", "filesystem.write_text",
        {"path": str(allowed_root / "report.txt")},
        ["filesystem:write"], approved=True,
    )
    assert decision["allowed"] is False
    assert "read_only" in decision["reason"]


def test_denied_tool_cannot_use_declared_broker_capability(tmp_path):
    policy = _policy(tmp_path)
    policy.data["denied_tools"] = ["file_reader"]
    decision = policy.evaluate_capability_request(
        "file_reader", "filesystem:read", "filesystem.read_text",
        {"path": str(tmp_path / "report.txt")}, ["filesystem:read"],
    )
    assert decision["allowed"] is False
    assert "denied" in decision["reason"].lower()
