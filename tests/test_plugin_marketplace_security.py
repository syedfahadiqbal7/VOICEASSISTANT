"""Security regression tests for plugin packaging and registration."""
from __future__ import annotations

import ast
import hashlib
import json
import stat
import zipfile
from pathlib import Path

from agent.plugin_marketplace import install_from_zip, validate_manifest
from agent.policy import PolicyEngine


def _package(tmp_path: Path, *, source: bytes = b"# safe plugin\\n",
             checksum: str | None = None, extra: dict[str, bytes] | None = None,
             manifest: bool = True) -> Path:
    archive = tmp_path / "plugin.zip"
    digest = checksum if checksum is not None else hashlib.sha256(source).hexdigest()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sample_plugin.py", source)
        if manifest:
            data = {
                "name": "sample_plugin", "version": "1.0.0",
                "description": "test", "author": "untrusted",
                "entry": "sample_plugin.py", "tools": ["sample_plugin"],
                "permissions": ["filesystem:read"], "checksum_sha256": digest,
            }
            zf.writestr("sample_plugin.manifest.json", json.dumps(data))
        for name, payload in (extra or {}).items():
            zf.writestr(name, payload)
    return archive


def test_manifest_rejects_unknown_permissions():
    data = {
        "name": "x", "version": "1.0.0", "entry": "x.py",
        "tools": ["x"], "permissions": ["filesystem:root"],
        "checksum_sha256": "a" * 64,
    }
    ok, errors = validate_manifest(data)
    assert not ok
    assert any("unsupported permissions" in item for item in errors)


def test_manifest_rejects_invalid_checksum_and_duplicate_tools():
    data = {
        "name": "x", "version": "1.0.0", "entry": "x.py",
        "tools": ["x", "x"], "permissions": [],
        "checksum_sha256": "not-a-hash",
    }
    ok, errors = validate_manifest(data)
    assert not ok
    assert any("checksum_sha256" in item for item in errors)
    assert any("tools" in item for item in errors)


def test_checksum_mismatch_is_rejected_without_rewriting(tmp_path):
    archive = _package(tmp_path, source=b"tampered", checksum="0" * 64)
    result = install_from_zip(archive, tmp_path / "plugins")
    assert result["ok"] is False
    assert "checksum mismatch" in " ".join(result["errors"])
    assert list((tmp_path / "plugins").glob("*.py")) == []


def test_zip_slip_path_is_rejected(tmp_path):
    archive = _package(tmp_path, extra={"../escaped.py": b"print('escaped')"})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert result["ok"] is False
    assert "unsafe archive path" in " ".join(result["errors"])
    assert not (tmp_path / "escaped.py").exists()


def test_unexpected_archive_files_are_rejected(tmp_path):
    archive = _package(tmp_path, extra={"payload.txt": b"unexpected"})
    result = install_from_zip(archive, tmp_path / "plugins")
    assert result["ok"] is False
    assert "unexpected files" in " ".join(result["errors"])


def test_symlink_archive_member_is_rejected(tmp_path):
    archive = _package(tmp_path)
    with zipfile.ZipFile(archive, "a") as zf:
        info = zipfile.ZipInfo("link.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "target.py")
    result = install_from_zip(archive, tmp_path / "plugins")
    assert result["ok"] is False
    assert "symbolic links" in " ".join(result["errors"])


def test_plugin_install_is_no_overwrite_by_default(tmp_path):
    archive = _package(tmp_path)
    plugins = tmp_path / "plugins"
    first = install_from_zip(archive, plugins)
    assert first["ok"] is True
    installed = Path(first["installed_entry"])
    original = installed.read_bytes()
    duplicate = install_from_zip(archive, plugins)
    assert duplicate["ok"] is False
    assert duplicate["requires_confirmation"] is True
    assert installed.read_bytes() == original


def test_explicit_plugin_replacement_updates_source_and_manifest(tmp_path):
    plugins = tmp_path / "plugins"
    first = install_from_zip(_package(tmp_path, source=b"# first\\n"), plugins)
    assert first["ok"] is True
    second = install_from_zip(_package(tmp_path, source=b"# second\\n"), plugins, replace=True)
    assert second["ok"] is True
    assert Path(second["installed_entry"]).read_bytes() == b"# second\\n"
    manifest = json.loads(Path(second["installed_manifest"]).read_text(encoding="utf-8"))
    assert manifest["checksum_sha256"] == hashlib.sha256(b"# second\\n").hexdigest()


def test_single_file_legacy_package_gets_a_generated_manifest(tmp_path):
    archive = _package(tmp_path, manifest=False)
    result = install_from_zip(archive, tmp_path / "plugins")
    assert result["ok"] is True
    assert Path(result["installed_manifest"]).is_file()
    data = json.loads(Path(result["installed_manifest"]).read_text(encoding="utf-8"))
    assert data["checksum_sha256"] == hashlib.sha256(b"# safe plugin\\n").hexdigest()


def test_read_only_policy_blocks_mutating_names_even_without_manifest_permissions(tmp_path):
    engine = PolicyEngine(path=tmp_path / "policy.json")
    engine.data["role"] = "read_only"
    for name in ("open_app", "forjob_create_tracker", "office_new", "teams_send_message"):
        assert engine.evaluate_tool_call(name, {}, permissions=[])["allowed"] is False
    assert engine.evaluate_tool_call("github_list_repos", {}, permissions=["network:read"])["allowed"] is True


def test_plugin_registry_does_not_partially_register_declared_tools(tmp_path, monkeypatch):
    import agent.tool_registry as registry_module

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    source = '''from tools.base import BaseTool
class OneTool(BaseTool):
    @property
    def name(self): return "only_one"
    @property
    def description(self): return "test"
    @property
    def parameters(self): return {"type": "object", "properties": {}}
    def execute(self, **kwargs): return {"content": "ok", "is_error": False}
'''
    entry = plugins / "atomic_test.py"
    entry.write_text(source, encoding="utf-8")
    manifest = {
        "name": "atomic_test", "version": "1.0.0", "entry": entry.name,
        "tools": ["only_one", "missing_tool"], "permissions": [],
        "checksum_sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
    }
    entry.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(registry_module, "PLUGINS_DIR", plugins)
    registry = registry_module.ToolRegistry()
    assert registry.get("only_one") is None
    assert registry.get("missing_tool") is None


def test_all_plugin_manifests_exactly_match_exported_tool_names():
    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    for entry in sorted(plugins_dir.glob("*.py")):
        if entry.name.startswith("__") or entry.name == "task_scheduler.py":
            continue
        tree = ast.parse(entry.read_text(encoding="utf-8"))
        exported = set()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            is_base_tool = any(
                (isinstance(base, ast.Name) and base.id == "BaseTool")
                or (isinstance(base, ast.Attribute) and base.attr == "BaseTool")
                for base in node.bases
            )
            if not is_base_tool:
                continue
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == "name":
                    for child in ast.walk(member):
                        if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant):
                            if isinstance(child.value.value, str):
                                exported.add(child.value.value)
        manifest_path = entry.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert exported == set(manifest.get("tools") or []), (
            f"{entry.name}: exports={sorted(exported)} manifest={manifest.get('tools')}"
        )


def test_manifest_author_claim_does_not_establish_publisher_trust(tmp_path):
    from agent.plugin_marketplace import verify_flat_plugin

    entry = tmp_path / "trusted_claim.py"
    source = b"# plugin source"
    entry.write_bytes(source)
    manifest = {
        "name": "trusted_claim", "version": "1.0.0", "entry": entry.name,
        "tools": ["trusted_claim"], "permissions": [],
        "author": "VoiceAssistant",
        "checksum_sha256": hashlib.sha256(source).hexdigest(),
    }
    entry.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = verify_flat_plugin(entry)
    assert result["ok"] is True
    assert result["trusted_author"] is False
    assert result["author_claimed_trusted"] is True
