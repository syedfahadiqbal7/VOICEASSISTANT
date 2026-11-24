# agent/plugin_marketplace.py
"""
Phase 3 — Plugin marketplace helpers: manifest + SHA-256 checksum verify/install.

Layout for a plugin package (folder or zip):
  manifest.json
  <entry.py>          # e.g. my_tool.py

manifest.json:
{
  "name": "my_tool",
  "version": "1.0.0",
  "description": "...",
  "author": "vendor",
  "entry": "my_tool.py",
  "tools": ["my_tool"],
  "permissions": ["network:read", "filesystem:write"],
  "checksum_sha256": "<hex of entry file bytes>"
}
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

try:
    from config import BASE_DIR, PLUGINS_DIR
except Exception:
    BASE_DIR = Path(".").resolve()
    PLUGINS_DIR = BASE_DIR / "plugins"

MANIFEST_NAME = "manifest.json"
TRUSTED_AUTHORS = {"VoiceAssistant", "local", "builtin"}
SUPPORTED_PERMISSIONS = {
    "filesystem:read", "filesystem:write", "filesystem:delete",
    "network:read", "network:write",
    "process:read", "process:execute", "process:terminate",
    "screen:read", "screen:control",
    "clipboard:read", "clipboard:write",
    "system:environment:write", "system:admin",
    "service:control", "docker:control", "registry:read", "registry:write",
    "email:read", "email:send", "messaging:read", "messaging:send",
}
MAX_PLUGIN_ARCHIVE_FILES = 20
MAX_PLUGIN_ARCHIVE_TOTAL_BYTES = 10 * 1024 * 1024
MAX_PLUGIN_SOURCE_BYTES = 5 * 1024 * 1024
SAFE_NO_PERMISSION_PLUGIN_FILES = {"example_hello_tool.py", "json_formatter.py"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def validate_manifest(manifest: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    for key in ("name", "version", "entry", "checksum_sha256"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            errors.append(f"missing or invalid field: {key}")
    entry = str(manifest.get("entry") or "")
    if entry and ("/" in entry or "\\" in entry or entry in {".", ".."} or ":" in entry):
        errors.append("entry must be a plain filename in the package root")
    name = str(manifest.get("name") or "")
    if name and not all(c.isalnum() or c in "_-" for c in name):
        errors.append("name must be alphanumeric/_/-")
    checksum = str(manifest.get("checksum_sha256") or "").strip()
    if checksum and (len(checksum) != 64 or any(c not in "0123456789abcdefABCDEF" for c in checksum)):
        errors.append("checksum_sha256 must be exactly 64 hexadecimal characters")
    tools = manifest.get("tools")
    if (
        not isinstance(tools, list) or not tools
        or not all(isinstance(x, str) and x.strip() and len(x) <= 128
                   and all(c.isalnum() or c in "_-" for c in x) for x in tools)
        or len(set(tools)) != len(tools)
    ):
        errors.append("tools must be a non-empty unique list of valid BaseTool.name values")
    execution_sandbox = manifest.get("execution_sandbox", "worker")
    if not isinstance(execution_sandbox, str) or execution_sandbox not in {"worker", "appcontainer"}:
        errors.append("execution_sandbox must be 'worker' or 'appcontainer'")
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(x, str) and x.strip() for x in permissions):
        errors.append("permissions must be a list of non-empty strings")
    else:
        normalized = [x.strip().lower() for x in permissions]
        if len(set(normalized)) != len(normalized):
            errors.append("permissions must not contain duplicates")
        unknown = sorted(set(normalized) - SUPPORTED_PERMISSIONS)
        if unknown:
            errors.append("unsupported permissions: " + ", ".join(unknown))
    return (len(errors) == 0, errors)

def verify_plugin_dir(plugin_dir: Path) -> Dict[str, Any]:
    """Verify manifest + checksum for a directory containing manifest + entry."""
    plugin_dir = Path(plugin_dir)
    try:
        plugin_root = plugin_dir.resolve()
        if plugin_dir.is_symlink():
            return {"ok": False, "errors": ["plugin directory must not be a symbolic link"], "path": str(plugin_dir)}
    except OSError as error:
        return {"ok": False, "errors": [f"plugin directory cannot be resolved: {error}"], "path": str(plugin_dir)}
    man_path = plugin_root / MANIFEST_NAME
    if man_path.is_symlink():
        return {"ok": False, "errors": ["manifest must not be a symbolic link"], "path": str(plugin_dir)}
    if not man_path.is_file():
        return {"ok": False, "errors": ["manifest.json not found"], "path": str(plugin_dir)}
    try:
        manifest = load_manifest(man_path)
    except Exception as e:
        return {"ok": False, "errors": [f"manifest parse error: {e}"], "path": str(plugin_dir)}
    ok, errors = validate_manifest(manifest)
    if not ok:
        return {"ok": False, "errors": errors, "manifest": manifest, "path": str(plugin_dir)}

    entry = plugin_root / str(manifest["entry"])
    if entry.is_symlink():
        return {"ok": False, "errors": ["plugin entry must not be a symbolic link"], "manifest": manifest, "path": str(plugin_dir)}
    if not entry.is_file() or entry.resolve().parent != plugin_root:
        return {
            "ok": False,
            "errors": [f"entry file missing: {manifest['entry']}"],
            "manifest": manifest,
            "path": str(plugin_dir),
        }
    digest = sha256_file(entry)
    expected = str(manifest.get("checksum_sha256") or "").lower().strip()
    if digest.lower() != expected:
        return {
            "ok": False,
            "errors": [
                f"checksum mismatch for {entry.name}: "
                f"expected {expected[:16]}… got {digest[:16]}…"
            ],
            "manifest": manifest,
            "path": str(plugin_dir),
            "actual_sha256": digest,
        }
    return {
        "ok": True,
        "manifest": manifest,
        "path": str(plugin_dir),
        "sha256": digest,
        "trusted_author": False,
        "author_claimed_trusted": str(manifest.get("author") or "") in TRUSTED_AUTHORS,
    }


def extract_plugin_tool_names(entry_file: Path) -> List[str]:
    """Read BaseTool.name exports without importing or executing plugin code."""
    tree = ast.parse(Path(entry_file).read_text(encoding="utf-8-sig"))
    names: List[str] = []
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
        tool_name = None
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "name":
                for child in ast.walk(member):
                    if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant):
                        if isinstance(child.value.value, str):
                            tool_name = child.value.value.strip()
        if tool_name:
            names.append(tool_name)
    if not names or len(names) != len(set(names)):
        raise ValueError("plugin must export one or more unique static BaseTool.name values")
    return sorted(names)


def build_manifest_for_file(
    entry_file: Path,
    name: Optional[str] = None,
    version: str = "1.0.0",
    description: str = "",
    author: str = "local",
    tools: Optional[List[str]] = None,
    permissions: Optional[List[str]] = None,
    execution_sandbox: Optional[str] = None,
) -> Dict[str, Any]:
    entry_file = Path(entry_file)
    digest = sha256_file(entry_file)
    manifest = {
        "name": name or entry_file.stem,
        "version": version,
        "description": description or f"Plugin {entry_file.stem}",
        "author": author,
        "entry": entry_file.name,
        "tools": tools or [entry_file.stem],
        "permissions": permissions or [],
        "checksum_sha256": digest,
    }
    if execution_sandbox is not None:
        if not isinstance(execution_sandbox, str) or execution_sandbox not in {"worker", "appcontainer"}:
            raise ValueError("execution_sandbox must be 'worker' or 'appcontainer'")
        manifest["execution_sandbox"] = execution_sandbox
    return manifest


def write_manifest_beside(entry_file: Path, **kwargs) -> Path:
    """Write manifest.json next to a plugin .py (same folder)."""
    entry_file = Path(entry_file)
    man = build_manifest_for_file(entry_file, **kwargs)
    # For flat plugins/ dir, store as <stem>.manifest.json to avoid collisions
    out = entry_file.with_suffix(".manifest.json")
    out.write_text(json.dumps(man, indent=2), encoding="utf-8")
    return out


def verify_flat_plugin(entry_file: Path, manifest_path: Optional[Path] = None) -> Dict[str, Any]:
    """Verify a flat plugins/<name>.py with plugins/<name>.manifest.json."""
    entry_file = Path(entry_file)
    man_path = Path(manifest_path) if manifest_path else entry_file.with_suffix(".manifest.json")
    try:
        plugin_root = entry_file.parent.resolve()
        if entry_file.is_symlink() or man_path.is_symlink():
            return {"ok": False, "errors": ["plugin source and manifest must not be symbolic links"], "path": str(entry_file)}
        if entry_file.resolve().parent != plugin_root or man_path.resolve().parent != plugin_root:
            return {"ok": False, "errors": ["plugin source and manifest must remain inside the plugin directory"], "path": str(entry_file)}
    except OSError as error:
        return {"ok": False, "errors": [f"plugin paths cannot be safely resolved: {error}"], "path": str(entry_file)}
    if not man_path.is_file():
        return {
            "ok": False,
            "errors": [f"no manifest for {entry_file.name}"],
            "path": str(entry_file),
            "skipped": True,
        }
    try:
        manifest = load_manifest(man_path)
    except Exception as e:
        return {"ok": False, "errors": [str(e)], "path": str(entry_file)}
    ok, errors = validate_manifest(manifest)
    if not ok:
        return {"ok": False, "errors": errors, "path": str(entry_file)}
    if Path(manifest["entry"]).name != entry_file.name:
        return {
            "ok": False,
            "errors": [f"manifest entry '{manifest['entry']}' != file '{entry_file.name}'"],
            "path": str(entry_file),
        }
    digest = sha256_file(entry_file)
    expected = str(manifest.get("checksum_sha256") or "").lower().strip()
    if digest.lower() != expected:
        return {
            "ok": False,
            "errors": [f"checksum mismatch: expected {expected[:16]}… got {digest[:16]}…"],
            "path": str(entry_file),
            "actual_sha256": digest,
            "manifest": manifest,
        }
    return {
        "ok": True,
        "path": str(entry_file),
        "manifest": manifest,
        "sha256": digest,
        "trusted_author": False,
        "author_claimed_trusted": str(manifest.get("author") or "") in TRUSTED_AUTHORS,
    }


def scan_plugins_dir(plugins_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(plugins_dir) if plugins_dir else Path(PLUGINS_DIR)
    results = []
    if not root.is_dir():
        return results
    for py in sorted(root.glob("*.py")):
        if py.name.startswith("_"):
            continue
        results.append(verify_flat_plugin(py))
    return results


def install_from_zip(
    zip_path: Path,
    plugins_dir: Optional[Path] = None,
    require_checksum: bool = True,
    replace: bool = False,
) -> Dict[str, Any]:
    """Safely validate and install a single-file Python plugin package.

    Archive members are read into a bounded staging directory; extractall is
    deliberately avoided. Existing plugins are never overwritten unless the
    caller explicitly opts in with replace=True.

    require_checksum is retained for API compatibility. A supplied manifest
    checksum is always enforced; this flag never bypasses a mismatch.
    """
    zip_path = Path(zip_path)
    dest_root = Path(plugins_dir) if plugins_dir else Path(PLUGINS_DIR)
    if not zip_path.is_file():
        return {"ok": False, "errors": [f"zip not found: {zip_path}"]}
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest_root = dest_root.resolve()
    except OSError as error:
        return {"ok": False, "errors": [f"plugin destination unavailable: {error}"]}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = [item for item in zf.infolist() if not item.is_dir()]
            if not infos:
                return {"ok": False, "errors": ["plugin archive is empty"]}
            if len(infos) > MAX_PLUGIN_ARCHIVE_FILES:
                return {"ok": False, "errors": ["plugin archive contains too many files"]}

            total_size = 0
            for info in infos:
                raw_name = info.filename
                if chr(0) in raw_name or chr(92) in raw_name:
                    return {"ok": False, "errors": [f"unsafe archive path: {raw_name!r}"]}
                member_path = PurePosixPath(raw_name)
                if (
                    member_path.is_absolute()
                    or not member_path.parts
                    or any(part in {"", ".", ".."} for part in member_path.parts)
                    or ":" in member_path.parts[0]
                ):
                    return {"ok": False, "errors": [f"unsafe archive path: {raw_name!r}"]}
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    return {"ok": False, "errors": [f"symbolic links are not allowed: {raw_name}"]}
                if info.flag_bits & 0x1:
                    return {"ok": False, "errors": [f"encrypted member is not supported: {raw_name}"]}
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    return {"ok": False, "errors": [f"unsupported compression method: {raw_name}"]}
                if info.file_size < 0 or info.file_size > MAX_PLUGIN_SOURCE_BYTES:
                    return {"ok": False, "errors": [f"archive member is too large: {raw_name}"]}
                total_size += info.file_size
                if total_size > MAX_PLUGIN_ARCHIVE_TOTAL_BYTES:
                    return {"ok": False, "errors": ["plugin archive exceeds the uncompressed size limit"]}

            names = [PurePosixPath(item.filename) for item in infos]
            manifests = [
                item for item in names
                if item.name == MANIFEST_NAME or item.name.endswith(".manifest.json")
            ]
            if len(manifests) > 1:
                return {"ok": False, "errors": ["plugin archive must contain exactly one manifest"]}
            python_files = [item for item in names if item.suffix.lower() == ".py"]
            if len(python_files) != 1:
                return {"ok": False, "errors": ["plugin archive must contain exactly one Python entry file"]}

            source_path = python_files[0]
            manifest_path = manifests[0] if manifests else None
            if manifest_path and manifest_path.parent != source_path.parent:
                return {"ok": False, "errors": ["manifest and Python entry must be in the same package folder"]}
            allowed_paths = {source_path}
            if manifest_path:
                allowed_paths.add(manifest_path)
            extras = [str(item) for item in names if item not in allowed_paths]
            if extras:
                return {"ok": False, "errors": ["unexpected files in plugin archive: " + ", ".join(extras[:5])]}

            source_info = next(item for item in infos if PurePosixPath(item.filename) == source_path)
            source_bytes = zf.read(source_info)
            if len(source_bytes) != source_info.file_size:
                return {"ok": False, "errors": ["plugin source size did not match archive metadata"]}

            if manifest_path:
                manifest_info = next(item for item in infos if PurePosixPath(item.filename) == manifest_path)
                manifest_bytes = zf.read(manifest_info)
                if len(manifest_bytes) > 64 * 1024:
                    return {"ok": False, "errors": ["plugin manifest is too large"]}
                try:
                    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
                except Exception as error:
                    return {"ok": False, "errors": [f"manifest parse error: {error}"]}
                if not isinstance(manifest, dict):
                    return {"ok": False, "errors": ["manifest must be a JSON object"]}
                valid, errors = validate_manifest(manifest)
                if not valid:
                    return {"ok": False, "errors": errors}
                if manifest.get("entry") != source_path.name:
                    return {"ok": False, "errors": ["manifest entry does not match the packaged Python file"]}
                actual_digest = hashlib.sha256(source_bytes).hexdigest()
                expected = str(manifest.get("checksum_sha256") or "").lower()
                if actual_digest != expected:
                    return {
                        "ok": False,
                        "errors": ["plugin checksum mismatch; package was not installed"],
                        "expected_sha256": expected,
                        "actual_sha256": actual_digest,
                    }
            else:
                # Legacy single-file packages remain supported; their manifest is
                # generated from the exact bytes that are installed.
                manifest = {
                    "name": source_path.stem,
                    "version": "1.0.0",
                    "description": f"Installed from zip: {source_path.name}",
                    "author": "local",
                    "entry": source_path.name,
                    "tools": [source_path.stem],
                    "permissions": ["system:admin"],
                    "checksum_sha256": hashlib.sha256(source_bytes).hexdigest(),
                }
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as error:
        return {"ok": False, "errors": [f"plugin archive validation failed: {error}"]}

    entry_name = source_path.name
    dest_py = dest_root / entry_name
    dest_man = dest_root / f"{Path(entry_name).stem}.manifest.json"
    if (dest_py.exists() or dest_man.exists()) and not replace:
        return {
            "ok": False,
            "requires_confirmation": True,
            "errors": [f"plugin '{Path(entry_name).stem}' already exists; explicit replacement is required"],
        }

    flat_manifest = dict(manifest)
    flat_manifest["entry"] = dest_py.name
    flat_manifest["checksum_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    manifest_bytes = (json.dumps(flat_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    try:
        with tempfile.TemporaryDirectory(prefix=".plugin-install-", dir=str(dest_root)) as td:
            stage_dir = Path(td)
            staged_py = stage_dir / dest_py.name
            staged_manifest = stage_dir / dest_man.name
            for staged_path, payload in ((staged_py, source_bytes), (staged_manifest, manifest_bytes)):
                with open(staged_path, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())

            if not replace:
                # Hard links are atomic and refuse to overwrite a concurrent install.
                try:
                    os.link(staged_py, dest_py)
                    try:
                        os.link(staged_manifest, dest_man)
                    except Exception:
                        dest_py.unlink(missing_ok=True)
                        raise
                except FileExistsError:
                    return {"ok": False, "requires_confirmation": True,
                            "errors": ["plugin destination already exists; explicit replacement is required"]}
            else:
                backups = {}
                for destination in (dest_py, dest_man):
                    if destination.exists():
                        backup = stage_dir / (destination.name + ".backup")
                        shutil.copy2(destination, backup)
                        backups[destination] = backup
                installed = []
                try:
                    os.replace(staged_py, dest_py)
                    installed.append(dest_py)
                    os.replace(staged_manifest, dest_man)
                    installed.append(dest_man)
                except Exception:
                    for destination in installed:
                        if destination in backups:
                            os.replace(backups[destination], destination)
                        else:
                            destination.unlink(missing_ok=True)
                    raise
    except Exception as error:
        return {"ok": False, "errors": [f"plugin install failed: {error}"]}

    return {
        "ok": True,
        "installed_entry": str(dest_py),
        "installed_manifest": str(dest_man),
        "manifest": flat_manifest,
        "message": f"Installed plugin '{flat_manifest.get('name')}' v{flat_manifest.get('version', '1.0.0')}. Restart app to load.",
    }

def generate_manifests_for_plugins_dir(
    plugins_dir: Optional[Path] = None,
    author: str = "VoiceAssistant",
) -> List[str]:
    """Regenerate checksums/tool exports without erasing existing permissions.

    New or legacy plugins without permission metadata receive system:admin so
    read-only roles and auto-approval paths fail closed until reviewed.
    """
    root = Path(plugins_dir) if plugins_dir else Path(PLUGINS_DIR)
    written = []
    for py in sorted(root.glob("*.py")):
        if py.name.startswith("_") or py.name == "task_scheduler.py":
            continue
        tool_names = extract_plugin_tool_names(py)
        manifest_path = py.with_suffix(".manifest.json")
        previous = {}
        if manifest_path.is_file():
            try:
                previous = load_manifest(manifest_path)
            except Exception:
                previous = {}
        permissions = previous.get("permissions")
        valid_permissions = (
            isinstance(permissions, list)
            and all(isinstance(item, str) and item.strip().lower() in SUPPORTED_PERMISSIONS for item in permissions)
            and len({item.strip().lower() for item in permissions}) == len(permissions)
        )
        if not valid_permissions or (
            not permissions and py.name not in SAFE_NO_PERMISSION_PLUGIN_FILES
        ):
            permissions = [] if py.name in SAFE_NO_PERMISSION_PLUGIN_FILES else ["system:admin"]
        path = write_manifest_beside(
            py,
            name=str(previous.get("name") or py.stem),
            version=str(previous.get("version") or "1.0.0"),
            author=str(previous.get("author") or author),
            description=str(previous.get("description") or f"Builtin/local plugin {py.stem}"),
            tools=tool_names,
            permissions=permissions,
        )
        written.append(str(path))
    return written
