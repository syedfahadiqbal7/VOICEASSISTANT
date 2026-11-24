"""One-shot plugin worker process.

This is crash containment only. It is NOT an OS security sandbox: the worker
still runs as the desktop user and may access resources available to that user.
The host must continue to enforce policy/approval before dispatch.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import inspect
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


class _DiscardTextWriter:
    """Discard plugin output without opening the Windows NUL device."""

    encoding = "utf-8"
    errors = "replace"

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def _emit(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        payload = {
            "ok": False,
            "error_code": "response_too_large",
            "error": "Plugin worker response exceeded the configured size limit.",
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _load_plugin(entry: Path, expected_sha256: str):
    from tools.base import BaseTool

    root = entry.parent.resolve(strict=True)
    if entry.is_symlink() or entry.resolve(strict=True).parent != root:
        raise ValueError("plugin source path is not a safe regular file")
    if not entry.is_file():
        raise ValueError("plugin source is not a regular file")
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    if digest.lower() != str(expected_sha256).lower():
        raise ValueError("plugin source changed after host verification")

    module_name = "voiceassistant_isolated_plugin"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ValueError("plugin module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    # Plugin stdout/stderr must never corrupt the host's machine-readable IPC.
    sink = _DiscardTextWriter()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        spec.loader.exec_module(module)
        exported = [
            (name, cls) for name, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, BaseTool) and cls is not BaseTool
            and cls.__module__ == module.__name__
        ]
        instances = [(class_name, cls, cls()) for class_name, cls in exported]
        for _, _, instance in instances:
            # Force metadata evaluation inside the worker, not the host.
            _ = instance.name
            _ = instance.description
            _ = instance.parameters
    return instances


def _main(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("protocol_version") != 1:
        raise ValueError("unsupported worker protocol")
    mode = request.get("mode")
    if mode not in {"describe", "execute"}:
        raise ValueError("unsupported worker operation")

    entry = Path(os.path.abspath(str(request.get("entry") or "")))
    expected = str(request.get("sha256") or "")
    if len(expected) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in expected):
        raise ValueError("invalid expected plugin digest")

    # The worker's own code lives under <repo>/agent. Add only the repository
    # root needed for the shared BaseTool contract; no current-directory imports.
    repository_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository_root))
    instances = _load_plugin(entry, expected)

    if mode == "describe":
        tools = []
        for class_name, _, instance in instances:
            help_value = getattr(instance, "help", None)
            if callable(help_value):
                help_value = help_value()
            tools.append({
                "class_name": class_name,
                "name": str(instance.name or "").strip(),
                "description": str(instance.description or ""),
                "parameters": instance.parameters,
                "help": help_value if isinstance(help_value, dict) else {},
            })
        return {"ok": True, "tools": tools}

    class_name = str(request.get("class_name") or "")
    tool_name = str(request.get("tool_name") or "")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    selected = [
        instance for exported_name, _, instance in instances
        if exported_name == class_name and str(instance.name).strip() == tool_name
    ]
    if len(selected) != 1:
        raise ValueError("requested plugin tool identity did not match one exported tool")
    sink = _DiscardTextWriter()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        result = selected[0].execute(**arguments)
    if not isinstance(result, dict):
        raise TypeError("plugin execute() must return a dictionary")
    # Enforce JSON-only IPC and reject opaque Python objects.
    json.dumps(result, ensure_ascii=False)
    return {"ok": True, "result": result}


def _process_request(raw: bytes) -> tuple[dict[str, Any], int]:
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        return {
            "ok": False,
            "error_code": "invalid_request",
            "error": "Invalid or oversized worker request.",
        }, 2
    try:
        request = json.loads(raw.decode("utf-8"))
        return _main(request), 0
    except BaseException as error:
        # Do not return tracebacks or plugin-controlled exception strings to the
        # desktop process; keep the protocol response categorical and bounded.
        return {
            "ok": False,
            "error_code": "plugin_worker_failed",
            "error": type(error).__name__,
        }, 1


def _encode_response(response: dict[str, Any]) -> bytes:
    encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({
            "ok": False,
            "error_code": "response_too_large",
            "error": "Plugin worker response exceeded the configured size limit.",
        }, separators=(",", ":")).encode("utf-8")
    return encoded


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--file-ipc":
        request_path = Path(sys.argv[2])
        response_path = Path(sys.argv[3])
        try:
            raw = request_path.read_bytes()
            # File-IPC mode is used by the AppContainer runner. Remove inherited
            # host secrets before plugin import, then restore only the minimum
            # Windows/Python environment and the SID-ACL broker credentials.
            try:
                request_for_environment = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                request_for_environment = {}
            system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or r"C:\\Windows"
            python_directory = str(Path(sys.executable).resolve().parent)
            safe_path = os.pathsep.join([
                str(Path(system_root) / "System32"),
                system_root,
                python_directory,
            ])
            work_directory = str(request_path.resolve().parent)
            os.environ.clear()
            os.environ.update({
                "SYSTEMROOT": system_root,
                "WINDIR": system_root,
                "PATH": safe_path,
                "TEMP": work_directory,
                "TMP": work_directory,
                "PYTHONIOENCODING": "utf-8",
            })
            broker = request_for_environment.get("broker") if isinstance(request_for_environment, dict) else None
            if broker is not None:
                if (
                    not isinstance(broker, dict)
                    or set(broker) != {"pipe", "token"}
                    or not isinstance(broker.get("pipe"), str)
                    or not isinstance(broker.get("token"), str)
                ):
                    raise ValueError("invalid host broker configuration")
                os.environ["VOICEASSISTANT_BROKER_PIPE"] = broker["pipe"]
                os.environ["VOICEASSISTANT_BROKER_TOKEN"] = broker["token"]
            response, exit_code = _process_request(raw)
            encoded = _encode_response(response)
            temporary = response_path.with_suffix(response_path.suffix + ".tmp")
            temporary.write_bytes(encoded)
            os.replace(temporary, response_path)
            return exit_code
        except BaseException as error:
            try:
                response_path.write_bytes(_encode_response({
                    "ok": False,
                    "error_code": "worker_file_ipc_failed",
                    "error": type(error).__name__,
                }))
            except Exception:
                pass
            return 3

    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    response, exit_code = _process_request(raw)
    _emit(response)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
