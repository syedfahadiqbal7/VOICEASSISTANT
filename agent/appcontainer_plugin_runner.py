"""Pilot AppContainer runner for plugins with a small, declared dependency surface.

The runner stages only the worker contract and one plugin file, grants a
per-invocation AppContainer SID read access to the staged source/runtime and
modify access to a private IPC directory, and uses the SID-ACL named-pipe broker.
Unsupported imports fail closed; there is no fallback to the same-user worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from agent.plugin_capability_broker import PluginCapabilityBroker
from security.windows_appcontainer import (
    _create_profile,
    _grant_acl,
    _remove_acl,
    _sid_string,
    _windows_apis,
    run_appcontainer_command,
)
from tools.plugin_capability_inventory import inspect_source


MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


class AppContainerPluginRunner:
    """Run a single low-dependency plugin in a no-capability AppContainer."""

    def __init__(self, policy):
        self.policy = policy

    def execute(
        self,
        entry: Path,
        sha256: str,
        class_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        permissions: list[str],
        timeout_seconds: int = 30,
        mode: str = "execute",
    ) -> dict[str, Any]:
        if mode not in {"execute", "describe"}:
            return {"ok": False, "error_code": "unsupported_worker_mode"}
        if os.name != "nt":
            return {"ok": False, "error_code": "appcontainer_unavailable"}
        original_entry = Path(entry)
        if original_entry.is_symlink():
            return {"ok": False, "error_code": "invalid_plugin_source"}
        try:
            entry = original_entry.resolve(strict=True)
        except (OSError, RuntimeError):
            return {"ok": False, "error_code": "invalid_plugin_source"}
        if not entry.is_file():
            return {"ok": False, "error_code": "invalid_plugin_source"}
        source_bytes = entry.read_bytes()
        actual_digest = hashlib.sha256(source_bytes).hexdigest()
        if actual_digest.lower() != str(sha256).lower():
            return {"ok": False, "error_code": "plugin_digest_mismatch"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error_code": "invalid_arguments"}
        normalized_permissions = [str(item).strip().lower() for item in (permissions or []) if str(item).strip()]
        if normalized_permissions and self.policy is None:
            return {"ok": False, "error_code": "host_policy_unavailable"}

        try:
            inventory = inspect_source(
                entry, {"permissions": permissions}, source_bytes=source_bytes
            )
        except Exception as error:
            return {
                "ok": False,
                "error_code": "plugin_static_inspection_failed",
                "error": type(error).__name__,
                "detail": str(error)[:240],
            }
        imports = set(inventory.get("imports") or [])
        unsupported_imports = sorted(imports - set(sys.stdlib_module_names) - {"tools"})
        risky_warnings = [
            item for item in inventory.get("warnings", [])
            if item.startswith(("dynamic_code_execution:", "dynamic_import", "native_or_COM_API:"))
        ]
        declared_permissions = set(normalized_permissions)
        observed_capabilities = set(inventory.get("observed_capabilities") or [])
        undeclared_capabilities = sorted(observed_capabilities - declared_permissions)
        unsupported_capabilities = sorted(observed_capabilities - {"filesystem:read"})
        unsupported_permissions = sorted(declared_permissions - {"filesystem:read"})
        if (
            unsupported_imports
            or risky_warnings
            or undeclared_capabilities
            or unsupported_capabilities
            or unsupported_permissions
        ):
            return {
                "ok": False,
                "error_code": "plugin_not_appcontainer_compatible",
                "unsupported_imports": unsupported_imports,
                "undeclared_capabilities": undeclared_capabilities,
                "unsupported_capabilities": unsupported_capabilities,
                "unsupported_permissions": unsupported_permissions,
                "warnings": risky_warnings,
            }

        kernel32, userenv, advapi32 = _windows_apis()
        profile_name = "VoiceAssistant.Plugin." + uuid.uuid4().hex
        sid = None
        sid_text = None
        broker = None
        root = Path(tempfile.mkdtemp(prefix="va-appcontainer-plugin-"))
        staging = root / "staging"
        work = root / "work"
        granted: list[Path] = []
        stage = "create_profile"
        try:
            sid = _create_profile(userenv, profile_name)
            stage = "stage_plugin_runtime"
            sid_text = _sid_string(kernel32, advapi32, sid)
            staging.mkdir()
            work.mkdir()

            for package in ("agent", "tools", "security", "plugins"):
                (staging / package).mkdir()
                (staging / package / "__init__.py").write_text("", encoding="utf-8")

            repo_root = Path(__file__).resolve().parents[1]
            for relative in (
                "agent/plugin_worker.py",
                "tools/base.py",
                "tools/plugin_capability_client.py",
                "security/windows_named_pipe.py",
            ):
                source = repo_root / relative
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

            staged_entry = staging / "plugins" / entry.name
            shutil.copy2(entry, staged_entry)
            if hashlib.sha256(staged_entry.read_bytes()).hexdigest().lower() != actual_digest.lower():
                raise ValueError("staged plugin checksum changed")

            broker_config = None
            if normalized_permissions:
                broker = PluginCapabilityBroker(self.policy, tool_name, normalized_permissions)
                broker_config = broker.start_named_pipe(sid_text)

            request_path = work / "request.json"
            response_path = work / "response.json"
            request = {
                "protocol_version": 1,
                "mode": mode,
                "entry": str(staged_entry),
                "sha256": actual_digest,
                "class_name": str(class_name),
                "tool_name": str(tool_name),
                "arguments": arguments,
                "broker": broker_config,
            }
            payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(payload) > MAX_REQUEST_BYTES:
                return {"ok": False, "error_code": "request_too_large"}
            request_path.write_bytes(payload)

            stage = "grant_root_acl"
            # AppContainer must be able to traverse every ancestor of its
            # private staging root. Grant execute/traverse only (never list/read)
            # on the user-temp path components; the unique SID is removed later.
            ancestors = list(root.parents)
            for ancestor in reversed(ancestors[:-1]):  # keep the volume root untouched
                _grant_acl(ancestor, sid_text, "(X)")
                granted.append(ancestor)
            _grant_acl(root, sid_text, "(X)")
            granted.append(root)
            runtime_roots = sorted(
                {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()},
                key=str,
            )
            stage = "grant_python_runtime_acl"
            for runtime_root in runtime_roots:
                _grant_acl(runtime_root, sid_text, "(OI)(CI)RX")
                granted.append(runtime_root)
            stage = "grant_staging_acl"
            # These files/directories were created before the parent ACL was
            # applied. Explicitly grant the existing tree; inheritance alone
            # does not retroactively fix every existing child DACL.
            _grant_acl(staging, sid_text, "(OI)(CI)RX")
            granted.append(staging)
            for child in sorted(staging.rglob("*"), key=lambda item: len(item.parts)):
                if child.is_dir():
                    _grant_acl(child, sid_text, "(OI)(CI)RX")
                else:
                    _grant_acl(child, sid_text, "(R)")
                granted.append(child)

            stage = "grant_ipc_acl"
            _grant_acl(work, sid_text, "(OI)(CI)M")
            granted.append(work)
            # The request file already exists; grant it explicitly. The
            # response and temporary response files will inherit from work.
            _grant_acl(request_path, sid_text, "(M)")
            granted.append(request_path)

            stage = "launch_appcontainer_worker"
            worker_path = staging / "agent" / "plugin_worker.py"
            system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\Windows"
            python_directory = str(Path(sys.executable).resolve().parent)
            safe_path = os.pathsep.join([
                str(Path(system_root) / "System32"),
                system_root,
                python_directory,
            ])
            # Do not inherit the desktop process environment. In particular,
            # API keys, cloud credentials, proxy secrets and Python startup
            # overrides must never reach the AppContainer worker.
            environment = {
                "SYSTEMROOT": system_root,
                "WINDIR": system_root,
                "PATH": safe_path,
                "TEMP": str(work),
                "TMP": str(work),
                "PYTHONIOENCODING": "utf-8",
            }
            exit_code = run_appcontainer_command(
                Path(sys.executable),
                ["-I", str(worker_path), "--file-ipc", str(request_path), str(response_path)],
                profile_name=profile_name,
                timeout_seconds=timeout_seconds,
                appcontainer_sid=sid,
                environment=environment,
                current_directory=work,
            )
            if not response_path.is_file():
                return {"ok": False, "error_code": "appcontainer_no_response", "exit_code": exit_code}
            raw_response = response_path.read_bytes()
            if len(raw_response) > MAX_RESPONSE_BYTES:
                return {"ok": False, "error_code": "appcontainer_response_too_large"}
            try:
                response = json.loads(raw_response.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {"ok": False, "error_code": "appcontainer_invalid_response"}
            if not isinstance(response, dict):
                return {"ok": False, "error_code": "appcontainer_invalid_response"}
            response["worker_exit_code"] = exit_code
            return response
        except TimeoutError:
            return {"ok": False, "error_code": "appcontainer_worker_timeout"}
        except Exception as error:
            return {
                "ok": False,
                "error_code": "appcontainer_worker_failed",
                "error": type(error).__name__,
                "stage": stage,
                "winerror": getattr(error, "winerror", None),
                "errno": getattr(error, "errno", None),
                "detail": str(error)[:240],
            }
        finally:
            if broker is not None:
                broker.close()
            if sid_text:
                for path in reversed(granted):
                    _remove_acl(path, sid_text)
            if sid is not None:
                userenv.DeleteAppContainerProfile(profile_name)
                advapi32.FreeSid(sid)
            shutil.rmtree(root, ignore_errors=True)

    def describe(
        self,
        entry: Path,
        sha256: str,
        permissions: list[str] | None = None,
        timeout_seconds: int = 15,
    ) -> dict[str, Any]:
        """Import and inspect plugin metadata inside AppContainer, never in the host worker."""
        return self.execute(
            entry=entry,
            sha256=sha256,
            class_name="",
            tool_name=Path(entry).stem,
            arguments={},
            permissions=list(permissions or []),
            timeout_seconds=timeout_seconds,
            mode="describe",
        )
