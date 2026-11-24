# agent/tool_registry.py
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Any

from tools.base import BaseTool
from tools.file_writer import FileWriterTool
from tools.powershell_executor import PowerShellExecutorTool
from tools.pip_installer import PipInstallerTool
from config import PLUGINS_DIR
from agent.plugin_marketplace import verify_flat_plugin, SAFE_NO_PERMISSION_PLUGIN_FILES
from agent.plugin_capability_broker import PluginCapabilityBroker
from agent.appcontainer_plugin_runner import AppContainerPluginRunner

# Plugins that must not load (name collision / superseded).
# task_scheduler.py conflicted with agent JobScheduler; use list_windows_tasks instead.
DISABLED_PLUGIN_FILES = {
    "task_scheduler.py",
}


PLUGIN_WORKER_TIMEOUT_SECONDS = 30
PLUGIN_WORKER_MAX_REQUEST_BYTES = 1024 * 1024
PLUGIN_WORKER_MAX_RESPONSE_BYTES = 1024 * 1024


def _create_windows_worker_job(process):
    """Assign a waiting worker to a bounded Job Object before sending its request.

    The worker blocks reading stdin until assignment completes, preventing plugin
    code from spawning descendants before the job is attached. This is resource
    and process-tree containment, not a filesystem/network security sandbox.
    """
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    limits = EXTENDED_LIMIT()
    limits.BasicLimitInformation.LimitFlags = 0x00000008 | 0x00000100 | 0x00000200 | 0x00002000
    limits.BasicLimitInformation.ActiveProcessLimit = 16
    limits.ProcessMemoryLimit = 2 * 1024 * 1024 * 1024
    limits.JobMemoryLimit = 3 * 1024 * 1024 * 1024

    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "AssignProcessToJobObject failed")
    return (kernel32, job)


def _close_windows_worker_job(job) -> None:
    if job is not None:
        kernel32, handle = job
        kernel32.CloseHandle(handle)  # KILL_ON_JOB_CLOSE also terminates descendants.


def _run_plugin_worker(request: Dict[str, Any], timeout: int = PLUGIN_WORKER_TIMEOUT_SECONDS, broker=None) -> Dict[str, Any]:
    """Run one plugin operation in a child process with bounded IPC and lifetime.

    Windows workers are assigned to a Job Object before plugin code is released:
    max 16 active processes, 2 GiB per process, 3 GiB per job, and kill-on-close.
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

    process = None
    job = None
    output = bytearray()
    overflow = threading.Event()

    def drain_stdout():
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                if len(output) + len(chunk) > PLUGIN_WORKER_MAX_RESPONSE_BYTES:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                elif not overflow.is_set():
                    output.extend(chunk)
        except (OSError, ValueError):
            pass

    try:
        if broker is not None:
            broker_config = broker.start()
            environment["VOICEASSISTANT_BROKER_HOST"] = broker_config["host"]
            environment["VOICEASSISTANT_BROKER_PORT"] = str(broker_config["port"])
            environment["VOICEASSISTANT_BROKER_TOKEN"] = broker_config["token"]
        process = subprocess.Popen(
            [sys.executable, "-I", str(worker_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(worker_path.parent.parent),
            env=environment,
            start_new_session=(os.name != "nt"),
        )
        # Child is blocked on stdin until the parent sends EOF. Attach OS
        # process-tree/resource controls before any plugin code can run.
        job = _create_windows_worker_job(process)
        reader = threading.Thread(target=drain_stdout, name="PluginWorkerOutput", daemon=True)
        reader.start()
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        finally:
            process.stdin.close()
        try:
            process.wait(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
            process.wait()
            reader.join(timeout=5)
            return {"ok": False, "error_code": "worker_timeout", "error": "Plugin worker timed out and was terminated."}
        reader.join(timeout=5)
        if reader.is_alive():
            process.kill()
            return {"ok": False, "error_code": "invalid_worker_response", "error": "Plugin worker output did not close."}
        if overflow.is_set():
            return {"ok": False, "error_code": "response_too_large", "error": "Plugin worker response exceeded the size limit."}
    except OSError as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        return {"ok": False, "error_code": "worker_start_failed", "error": type(error).__name__}
    finally:
        _close_windows_worker_job(job)
        if broker is not None:
            broker.close()

    try:
        response = json.loads(bytes(output).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error_code": "invalid_worker_response", "error": "Plugin worker returned an invalid response."}
    if not isinstance(response, dict) or response.get("ok") is not True:
        return response if isinstance(response, dict) else {"ok": False, "error_code": "invalid_worker_response"}
    return response


class PluginWorkerTool(BaseTool):
    """Host-side tool proxy; plugin code is imported/executed only in a child."""
    def __init__(
        self,
        entry: Path,
        sha256: str,
        class_name: str,
        metadata: Dict[str, Any],
        permissions: List[str],
        sandbox_mode: str = "worker",
    ):
        self._entry = str(Path(entry).resolve())
        self._sha256 = str(sha256)
        self._class_name = str(class_name)
        self._name = str(metadata["name"])
        self._description = str(metadata.get("description") or "")
        self._parameters = metadata.get("parameters") or {"type": "object", "properties": {}}
        self._help = metadata.get("help") if isinstance(metadata.get("help"), dict) else {}
        self._permissions = list(permissions or [])
        self._sandbox_mode = str(sandbox_mode or "worker").strip().lower()
        if self._sandbox_mode not in {"worker", "appcontainer"}:
            raise ValueError("sandbox_mode must be 'worker' or 'appcontainer'")
        self._policy = None

    def set_policy(self, policy) -> None:
        self._policy = policy

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
        if self._sandbox_mode == "appcontainer":
            response = AppContainerPluginRunner(self._policy).execute(
                entry=Path(self._entry),
                sha256=self._sha256,
                class_name=self._class_name,
                tool_name=self.name,
                arguments=kwargs,
                permissions=self._permissions,
                timeout_seconds=PLUGIN_WORKER_TIMEOUT_SECONDS,
            )
        else:
            broker = (
                PluginCapabilityBroker(self._policy, self.name, self._permissions)
                if self._policy is not None else None
            )
            response = _run_plugin_worker({
                "protocol_version": 1,
                "mode": "execute",
                "entry": self._entry,
                "sha256": self._sha256,
                "class_name": self._class_name,
                "tool_name": self.name,
                "arguments": kwargs,
            }, broker=broker)
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
        self._policy = None
        self.dry_run = dry_run
        self._register_default_tools()
        self._load_plugins()

    def _register_default_tools(self):
        self.register(FileWriterTool())
        self.register(PowerShellExecutorTool(dry_run=self.dry_run))
        self.register(PipInstallerTool())

    def set_policy(self, policy) -> None:
        """Attach the host-owned policy engine to every plugin proxy."""
        self._policy = policy
        for tool in self._tools.values():
            if isinstance(tool, PluginWorkerTool):
                tool.set_policy(policy)

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
        """Verify plugins in the host and inspect them in their declared execution boundary.

        The default worker is crash containment only. A manifest can explicitly
        request AppContainer; metadata discovery and every invocation then use
        AppContainer, and failures never fall back to the same-user worker.
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
                permissions = list(manifest.get("permissions") or [])
                if not permissions and file.name not in SAFE_NO_PERMISSION_PLUGIN_FILES:
                    permissions = ["system:admin"]
                sandbox_mode = str(manifest.get("execution_sandbox") or "worker").strip().lower()
                if sandbox_mode not in {"worker", "appcontainer"}:
                    raise ValueError("execution_sandbox must be 'worker' or 'appcontainer'")
                print(f"[Plugin] Verified {file.name} v{ver} ({sandbox_mode})")

                if sandbox_mode == "appcontainer":
                    response = AppContainerPluginRunner(self._policy).describe(
                        entry=file,
                        sha256=verification.get("sha256"),
                        permissions=permissions,
                        timeout_seconds=15,
                    )
                else:
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
                # Build the full registration set before mutating the registry.
                proxies = []
                for item in exported:
                    if not isinstance(item.get("parameters"), dict):
                        raise ValueError("plugin tool parameters must be a JSON object")
                    proxies.append(PluginWorkerTool(
                        file, verification["sha256"], str(item.get("class_name") or ""),
                        item, permissions=permissions, sandbox_mode=sandbox_mode,
                    ))
                for proxy in proxies:
                    self.register(proxy, permissions=permissions)
                    print(f"[Plugin] Registered: {proxy.name} ({sandbox_mode})")
            except Exception as error:
                print(f"[Plugin Error] Failed to load {file.name}: {type(error).__name__}: {error}")
