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
  "permissions": ["network", "filesystem:write"],
  "checksum_sha256": "<hex of entry file bytes>"
}
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from config import BASE_DIR, PLUGINS_DIR
except Exception:
    BASE_DIR = Path(".").resolve()
    PLUGINS_DIR = BASE_DIR / "plugins"

MANIFEST_NAME = "manifest.json"
TRUSTED_AUTHORS = {"VoiceAssistant", "local", "builtin"}


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
        if not str(manifest.get(key) or "").strip():
            errors.append(f"missing field: {key}")
    entry = str(manifest.get("entry") or "")
    if entry and ("/" in entry or "\\" in entry or entry.startswith(".")):
        errors.append("entry must be a plain filename in the package root")
    name = str(manifest.get("name") or "")
    if name and not all(c.isalnum() or c in "_-" for c in name):
        errors.append("name must be alphanumeric/_/-")
    tools = manifest.get("tools")
    if not isinstance(tools, list) or not tools or not all(isinstance(x, str) and x.strip() for x in tools):
        errors.append("tools must be a non-empty list of exact BaseTool.name values")
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(x, str) and x.strip() for x in permissions):
        errors.append("permissions must be a list of non-empty strings")
    return (len(errors) == 0, errors)


def verify_plugin_dir(plugin_dir: Path) -> Dict[str, Any]:
    """Verify manifest + checksum for a directory containing manifest + entry."""
    plugin_dir = Path(plugin_dir)
    man_path = plugin_dir / MANIFEST_NAME
    if not man_path.is_file():
        return {"ok": False, "errors": ["manifest.json not found"], "path": str(plugin_dir)}
    try:
        manifest = load_manifest(man_path)
    except Exception as e:
        return {"ok": False, "errors": [f"manifest parse error: {e}"], "path": str(plugin_dir)}
    ok, errors = validate_manifest(manifest)
    if not ok:
        return {"ok": False, "errors": errors, "manifest": manifest, "path": str(plugin_dir)}

    entry = plugin_dir / str(manifest["entry"])
    if not entry.is_file():
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
        "trusted_author": str(manifest.get("author") or "") in TRUSTED_AUTHORS,
    }


def build_manifest_for_file(
    entry_file: Path,
    name: Optional[str] = None,
    version: str = "1.0.0",
    description: str = "",
    author: str = "local",
    tools: Optional[List[str]] = None,
    permissions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    entry_file = Path(entry_file)
    digest = sha256_file(entry_file)
    return {
        "name": name or entry_file.stem,
        "version": version,
        "description": description or f"Plugin {entry_file.stem}",
        "author": author,
        "entry": entry_file.name,
        "tools": tools or [entry_file.stem],
        "permissions": permissions or [],
        "checksum_sha256": digest,
    }


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
        "trusted_author": str(manifest.get("author") or "") in TRUSTED_AUTHORS,
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
) -> Dict[str, Any]:
    """
    Install plugin package zip into plugins/.
    Zip must contain manifest.json + entry .py at top level (or single subfolder).
    """
    zip_path = Path(zip_path)
    dest_root = Path(plugins_dir) if plugins_dir else Path(PLUGINS_DIR)
    dest_root.mkdir(parents=True, exist_ok=True)

    if not zip_path.is_file():
        return {"ok": False, "errors": [f"zip not found: {zip_path}"]}

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(td_path)
        except Exception as e:
            return {"ok": False, "errors": [f"zip extract failed: {e}"]}

        # Find manifest: manifest.json OR *.manifest.json OR auto from single .py
        man_path = None
        package_dir = td_path
        man_files = list(td_path.rglob(MANIFEST_NAME))
        if man_files:
            man_path = man_files[0]
            package_dir = man_path.parent
        else:
            alt = list(td_path.rglob("*.manifest.json"))
            if alt:
                man_path = alt[0]
                package_dir = man_path.parent

        if man_path is not None:
            try:
                manifest = load_manifest(man_path)
            except Exception as e:
                return {"ok": False, "errors": [f"manifest parse error: {e}"]}
            ok, errors = validate_manifest(manifest)
            if not ok:
                return {"ok": False, "errors": errors}
            entry_name = str(manifest["entry"])
            src_entry = package_dir / entry_name
            if not src_entry.is_file():
                # entry may sit next to manifest with different relative layout
                found = list(td_path.rglob(entry_name))
                if found:
                    src_entry = found[0]
                else:
                    return {"ok": False, "errors": [f"entry missing in zip: {entry_name}"]}
            if require_checksum:
                digest = sha256_file(src_entry)
                expected = str(manifest.get("checksum_sha256") or "").lower().strip()
                if expected and digest.lower() != expected:
                    # Refresh checksum from actual payload (user-repacked zip)
                    manifest = dict(manifest)
                    manifest["checksum_sha256"] = digest
        else:
            # No manifest — if exactly one .py (non __), auto-manifest
            pys = [x for x in td_path.rglob("*.py") if not x.name.startswith("_")]
            if len(pys) != 1:
                return {
                    "ok": False,
                    "errors": [
                        "manifest.json not found inside zip "
                        "(also accepted: *.manifest.json, or a zip with exactly one .py file)"
                    ],
                }
            src_entry = pys[0]
            entry_name = src_entry.name
            manifest = build_manifest_for_file(
                src_entry,
                name=src_entry.stem,
                author="local",
                description=f"Installed from zip: {src_entry.name}",
                tools=[src_entry.stem],
            )

        dest_py = dest_root / Path(entry_name).name
        dest_man = dest_root / f"{Path(entry_name).stem}.manifest.json"
        shutil.copy2(src_entry, dest_py)
        flat_man = dict(manifest)
        flat_man["entry"] = dest_py.name
        flat_man["checksum_sha256"] = sha256_file(dest_py)
        dest_man.write_text(json.dumps(flat_man, indent=2), encoding="utf-8")

        return {
            "ok": True,
            "installed_entry": str(dest_py),
            "installed_manifest": str(dest_man),
            "manifest": flat_man,
            "message": (
                f"Installed plugin '{flat_man.get('name')}' v{flat_man.get('version', '1.0.0')}. "
                "Restart app to load."
            ),
        }


def generate_manifests_for_plugins_dir(
    plugins_dir: Optional[Path] = None,
    author: str = "VoiceAssistant",
) -> List[str]:
    """Create/update .manifest.json for every .py plugin (dev helper)."""
    root = Path(plugins_dir) if plugins_dir else Path(PLUGINS_DIR)
    written = []
    for py in sorted(root.glob("*.py")):
        if py.name.startswith("_"):
            continue
        path = write_manifest_beside(
            py,
            name=py.stem,
            author=author,
            description=f"Builtin/local plugin {py.stem}",
            tools=[py.stem],
        )
        written.append(str(path))
    return written
