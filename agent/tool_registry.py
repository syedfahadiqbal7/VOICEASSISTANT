# agent/tool_registry.py
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any

from tools.base import BaseTool
from tools.file_writer import FileWriterTool
from tools.powershell_executor import PowerShellExecutorTool
from tools.pip_installer import PipInstallerTool
from config import PLUGINS_DIR
from agent.plugin_marketplace import verify_flat_plugin, SAFE_NO_PERMISSION_PLUGIN_FILES

# Plugins that must not load (name collision / superseded).
# task_scheduler.py conflicted with agent JobScheduler; use list_windows_tasks instead.
DISABLED_PLUGIN_FILES = {
    "task_scheduler.py",
}


PLUGIN_WORKER_TIMEOUT_SECONDS = 30
PLUGIN_WORKER_MAX_REQUEST_BYTES = 1024 * 1024
PLUGIN_WORKER_MAX_RESPONSE_BYTES = 1024 * 1024


def _run_plugin_worker(request: Dict[str, Any], timeout: int = PLUGIN_WORKER_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run one plugin operation in a child process (crash containment only).

    This is not a sandbox: the child retains the desktop user's OS privileges.
    """
    worker_path = Path(__file__).with_name("plugin_worker.py").resolve()
    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > PLUGIN_WORKER_MAX_REQUEST_BYTES:
        return {"ok": False, "error_code": "request_too_large", "error": "Plugin request exceeded the size limit."}
    environment = {}
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(worker_path)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(worker_path.parent.parent),
            env=environment,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_code": "worker_timeout", "error": "Plugin worker timed out and was terminated."}
    except OSError as error:
        return {"ok": False, "error_code": "worker_start_failed", "error": type(error).__name__}
    if len(completed.stdout) > PLUGIN_WORKER_MAX_RESPONSE_BYTES:
        return {"ok": False, "error_code": "response_too_large", "error": "Plugin worker response exceeded the size limit."}
    try:
        response = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error_code": "invalid_worker_response", "error": "Plugin worker returned an invalid response."}
    if not isinstance(response, dict) or response.get("ok") is not True:
        return response if isinstance(response, dict) else {"ok": False, "error_code": "invalid_worker_response"}
    return response


class PluginWorkerTool(BaseTool):
    """Host-side tool proxy; plugin code is imported/executed only in a child."""
    def __init__(self, entry: Path, sha256: str, class_name: str, metadata: Dict[str, Any]):
        self._entry = str(Path(entry).resolve())
        self._sha256 = str(sha256)
        self._class_name = str(class_name)
        self._name = str(metadata["name"])
        self._description = str(metadata.get("description") or "")
        self._parameters = metadata.get("parameters") or {"type": "object", "properties": {}}
        self._help = metadata.get("help") if isinstance(metadata.get("help"), dict) else {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> Dict[str, Any]:
        return self._parameters

    @property
    def help(self) -> Dict[str, str]:
        return {
            "summary": str(self._help.get("summary") or self.description),
            "usage": str(self._help.get("usage") or "No detailed usage provided."),
            "examples": str(self._help.get("examples") or "No examples provided."),
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        response = _run_plugin_worker({
            "protocol_version": 1,
            "mode": "execute",
            "entry": self._entry,
            "sha256": self._sha256,
            "class_name": self._class_name,
            "tool_name": self.name,
            "arguments": kwargs,
        })
        if response.get("ok") is not True:
            return {
                "content": "Plugin worker failed: " + str(response.get("error") or response.get("error_code") or "unknown error"),
                "is_error": True,
                "error_code": str(response.get("error_code") or "plugin_worker_failed"),
            }
        result = response.get("result")
        if not isinstance(result, dict):
            return {"content": "Plugin worker returned an invalid result.", "is_error": True}
        return result


class ToolRegistry:
    def __init__(self, dry_run: bool = False):
        self._tools: Dict[str, BaseTool] = {}
        self._tool_permissions: Dict[str, List[str]] = {}
        self.dry_run = dry_run
        self._register_default_tools()
        self._load_plugins()

    def _register_default_tools(self):
        self.register(FileWriterTool())
        self.register(PowerShellExecutorTool(dry_run=self.dry_run))
        self.register(PipInstallerTool())

    def set_dry_run(self, enabled: bool):
        """Enable or disable dry-run mode for PowerShell tool."""
        self.dry_run = enabled
        if "execute_powershell_admin" in self._tools:
            tool = self._tools["execute_powershell_admin"]
            if hasattr(tool, "dry_run"):
                tool.dry_run = enabled

    def register(self, tool: BaseTool, permissions: List[str] | None = None):
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            raise ValueError("Cannot register a tool without a name")
        if name in self._tools:
            raise ValueError(f"Duplicate tool name '{name}' rejected; existing registration preserved")
        self._tools[name] = tool
        self._tool_permissions[name] = [str(p).strip().lower() for p in (permissions or []) if str(p).strip()]

    def get_permissions(self, name: str) -> List[str]:
        return list(self._tool_permissions.get(name, []))

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_openai_schemas(self) -> List[Dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = self.get(name)
        if not tool:
            return {"content": f"Unknown tool: {name}", "is_error": True}
        return tool.execute(**arguments)

    def get_all_help(self) -> List[Dict[str, str]]:
        """Return help information of all registered tools."""
        help_list = []
        for tool in self._tools.values():
            h = getattr(tool, "help", None)
            if callable(h):
                h = h()
            elif h is None:
                h = {
                    "summary": tool.description,
                    "usage": "No detailed usage provided.",
                    "examples": "No examples provided.",
                }
            help_list.append({
                "name": tool.name,
                "summary": h.get("summary", tool.description),
                "usage": h.get("usage", ""),
                "examples": h.get("examples", ""),
            })
        return help_list

    def _load_plugins(self):
        """Verify plugins in the host; inspect/import them only in a child worker.

        Worker subprocesses provide crash containment, not an OS security sandbox.
        Plugins still run with the desktop user's privileges and remain trusted code.
        """
        if not PLUGINS_DIR.exists():
            return

        for file in sorted(PLUGINS_DIR.glob("*.py")):
            if file.name.startswith("__"):
                continue
            if file.name in DISABLED_PLUGIN_FILES:
                print(f"[Plugin] Skipped (disabled): {file.name}")
                continue
            try:
                verification = verify_flat_plugin(file)
                if verification.get("skipped"):
                    print(f"[Plugin] BLOCKED (manifest required): {file.name}")
                    continue
                if not verification.get("ok"):
                    print(
                        f"[Plugin] BLOCKED (manifest/checksum): {file.name} — "
                        + "; ".join(verification.get("errors") or ["verify failed"])
                    )
                    continue
                manifest = verification.get("manifest") or {}
                declared_tools = set(manifest.get("tools") or [])
                ver = manifest.get("version", "?")
                print(f"[Plugin] Verified {file.name} v{ver}")

                response = _run_plugin_worker({
                    "protocol_version": 1,
                    "mode": "describe",
                    "entry": str(file.resolve()),
                    "sha256": verification.get("sha256"),
                }, timeout=15)
                if response.get("ok") is not True:
                    raise ValueError("worker metadata inspection failed: " + str(response.get("error_code") or response.get("error")))
                exported = response.get("tools")
                if not isinstance(exported, list) or not exported:
                    raise ValueError("plugin worker reported no exported tools")
                actual_names = [str(item.get("name") or "").strip() for item in exported if isinstance(item, dict)]
                if (
                    len(actual_names) != len(exported)
                    or any(not name for name in actual_names)
                    or len(actual_names) != len(set(actual_names))
                    or set(actual_names) != declared_tools
                ):
                    raise ValueError(
                        f"declared tools {sorted(declared_tools)} do not exactly match "
                        f"plugin exports {sorted(actual_names)}"
                    )
                if "task_scheduler" in actual_names:
                    raise ValueError("legacy tool name 'task_scheduler' is disabled")
                conflicts = sorted(set(actual_names) & set(self._tools))
                if conflicts:
                    raise ValueError("tool name conflict(s): " + ", ".join(conflicts))
                permissions = list(manifest.get("permissions") or [])
                if not permissions and file.name not in SAFE_NO_PERMISSION_PLUGIN_FILES:
                    permissions = ["system:admin"]

                # Build the full registration set before mutating the registry.
                proxies = []
                for item in exported:
                    if not isinstance(item.get("parameters"), dict):
                        raise ValueError("plugin tool parameters must be a JSON object")
                    proxies.append(PluginWorkerTool(
                        file, verification["sha256"], str(item.get("class_name") or ""), item
                    ))
                for proxy in proxies:
                    self.register(proxy, permissions=permissions)
                    print(f"[Plugin] Worker registered: {proxy.name} (crash containment only)")
            except Exception as error:
                print(f"[Plugin Error] Failed to load {file.name}: {type(error).__name__}: {error}")
