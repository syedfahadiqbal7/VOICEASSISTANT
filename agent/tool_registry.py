# agent/tool_registry.py
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Dict, List, Any

from tools.base import BaseTool
from tools.file_writer import FileWriterTool
from tools.powershell_executor import PowerShellExecutorTool
from tools.pip_installer import PipInstallerTool
from config import PLUGINS_DIR
from agent.plugin_marketplace import verify_flat_plugin

# Plugins that must not load (name collision / superseded).
# task_scheduler.py conflicted with agent JobScheduler; use list_windows_tasks instead.
DISABLED_PLUGIN_FILES = {
    "task_scheduler.py",
}


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
        """Automatically load all tools from the plugins/ folder."""
        if not PLUGINS_DIR.exists():
            return

        for file in PLUGINS_DIR.glob("*.py"):
            if file.name.startswith("__"):
                continue
            if file.name in DISABLED_PLUGIN_FILES:
                print(f"[Plugin] Skipped (disabled): {file.name}")
                continue
            try:
                # Plugins must have a valid manifest and matching checksum.
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
                except Exception as e:
                    print(f"[Plugin] verify error {file.name}: {e}; plugin blocked")
                    continue

                spec = importlib.util.spec_from_file_location(file.stem, file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[file.stem] = module
                spec.loader.exec_module(module)

                exported_classes = [
                    (name, obj) for name, obj in inspect.getmembers(module, inspect.isclass)
                    if issubclass(obj, BaseTool) and obj is not BaseTool
                    and obj.__module__ == module.__name__
                ]
                instances = [obj() for _, obj in exported_classes]
                actual_names = [str(getattr(item, "name", "") or "").strip() for item in instances]
                if (
                    not instances
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
                permissions = manifest.get("permissions") or []
                for tool_instance in instances:
                    self.register(tool_instance, permissions=permissions)
                    print(f"[Plugin] Loaded: {tool_instance.name}")
            except Exception as e:
                print(f"[Plugin Error] Failed to load {file.name}: {e}")
