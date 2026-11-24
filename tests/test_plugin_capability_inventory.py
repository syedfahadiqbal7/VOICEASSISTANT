from __future__ import annotations

import json
from pathlib import Path

from tools.plugin_capability_inventory import build_inventory, inspect_source, render_markdown


def test_inventory_uses_ast_without_importing_plugin(tmp_path):
    marker = tmp_path / "must_not_exist.txt"
    source = tmp_path / "probe.py"
    source.write_text(
        "from pathlib import Path\\n"
        "import subprocess\\n"
        f"Path({str(marker)!r}).write_text('executed')\\n"
        "def run():\\n"
        "    subprocess.run(['whoami'])\\n"
        "    Path('report.txt').read_text()\\n",
        encoding="utf-8",
    )
    result = inspect_source(source, {"permissions": ["filesystem:read"]})
    assert not marker.exists()
    assert "filesystem:read" in result["observed_capabilities"]
    assert "process:execute" in result["observed_capabilities"]
    assert result["undeclared_observed"] == ["process:execute"]


def test_inventory_reports_manifest_checksum_mismatch_and_missing_permissions(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    source = plugins / "probe.py"
    source.write_text("import requests\\nrequests.get('https://example.invalid')\\n", encoding="utf-8")
    manifest = {
        "name": "probe",
        "permissions": [],
        "tools": ["probe"],
        "checksum_sha256": "0" * 64,
    }
    source.with_suffix(".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    inventory = build_inventory(plugins)
    assert inventory["plugin_count"] == 1
    row = inventory["plugins"][0]
    assert row["checksum_matches"] is False
    assert "network:read" in row["undeclared_observed"]
    assert "network:read" in row["observed_capabilities"]


def test_inventory_flags_dynamic_execution_and_native_apis(tmp_path):
    source = tmp_path / "risky.py"
    source.write_text("import ctypes\\nexec('print(1)')\\n", encoding="utf-8")
    result = inspect_source(source, {"permissions": ["system:admin"]})
    assert "native_or_COM_API:ctypes" in result["warnings"]
    assert "dynamic_code_execution:exec" in result["warnings"]


def test_markdown_inventory_is_deterministic_and_includes_limitations(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "safe.py").write_text("def run():\\n    return 1\\n", encoding="utf-8")
    inventory = build_inventory(plugins)
    markdown = render_markdown(inventory)
    assert "Plugins scanned:** 1" in markdown
    assert "not a security certification" in markdown
    assert "safe" in markdown
