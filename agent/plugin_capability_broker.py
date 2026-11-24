"""Host-owned per-invocation plugin capability broker.

TCP is used for ordinary crash-contained workers. Windows AppContainer workers
use a package-SID ACL named pipe; AppContainer loopback networking stays disabled.
Neither transport alone is an OS sandbox.
"""
from __future__ import annotations

import base64
import hmac
import json
import secrets
import socketserver
import threading
from pathlib import Path
from typing import Any

from security.windows_named_pipe import WindowsNamedPipeServer


MAX_BROKER_REQUEST_BYTES = 64 * 1024
MAX_BROKER_RESPONSE_BYTES = 1024 * 1024
MAX_BROKER_FILE_BYTES = 512 * 1024


class _BrokerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 8


class PluginCapabilityBroker:
    """One-plugin, one-tool, one-worker broker with a random per-call secret."""

    def __init__(self, policy, tool_name: str, declared_permissions: list[str]):
        self.policy = policy
        self.tool_name = str(tool_name)
        self.declared_permissions = [str(item).strip().lower() for item in declared_permissions]
        self._token = secrets.token_urlsafe(32)
        self._server = None
        self._pipe_server = None
        self._thread = None
        self._seen_request_ids = set()
        self._seen_lock = threading.Lock()

    def _handle_payload(self, raw: bytes) -> bytes:
        def reply(payload: dict[str, Any]) -> bytes:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(encoded) > MAX_BROKER_RESPONSE_BYTES:
                return b'{"ok":false,"error_code":"response_too_large"}\n'
            return encoded

        if not raw or len(raw) > MAX_BROKER_REQUEST_BYTES or not raw.endswith(b"\n"):
            return reply({"ok": False, "error_code": "invalid_request"})
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return reply({"ok": False, "error_code": "invalid_request"})
        if not isinstance(request, dict):
            return reply({"ok": False, "error_code": "invalid_request"})
        allowed_fields = {
            "protocol_version", "request_id", "token",
            "capability", "operation", "arguments",
        }
        if set(request) != allowed_fields or request.get("protocol_version") != 1:
            return reply({"ok": False, "error_code": "invalid_request"})
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return reply({"ok": False, "error_code": "invalid_request"})
        supplied_token = request.get("token")
        if not isinstance(supplied_token, str) or not hmac.compare_digest(supplied_token, self._token):
            return reply({"ok": False, "request_id": request_id, "error_code": "unauthorized"})
        with self._seen_lock:
            if request_id in self._seen_request_ids:
                return reply({"ok": False, "request_id": request_id, "error_code": "replayed_request"})
            if len(self._seen_request_ids) >= 4096:
                return reply({"ok": False, "request_id": request_id, "error_code": "request_limit_reached"})
            self._seen_request_ids.add(request_id)
        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            return reply({"ok": False, "request_id": request_id, "error_code": "invalid_arguments"})

        decision = self.policy.evaluate_capability_request(
            self.tool_name,
            str(request.get("capability") or ""),
            str(request.get("operation") or ""),
            arguments,
            declared_permissions=self.declared_permissions,
            approved=False,  # Approval is host-owned; never accepted from plugin IPC.
        )
        if not decision.get("allowed"):
            return reply({
                "ok": False,
                "request_id": request_id,
                "error_code": "capability_denied",
                "reason": str(decision.get("reason") or "denied"),
            })
        try:
            result = self._dispatch_read(str(request.get("operation") or ""), arguments)
            return reply({"ok": True, "request_id": request_id, "result": result})
        except FileNotFoundError:
            return reply({"ok": False, "request_id": request_id, "error_code": "not_found"})
        except (OSError, UnicodeError, ValueError) as error:
            return reply({
                "ok": False,
                "request_id": request_id,
                "error_code": "broker_operation_failed",
                "reason": type(error).__name__,
            })

    def start(self) -> dict[str, Any]:
        if self._server is not None or self._pipe_server is not None:
            raise RuntimeError("broker has already been started")
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                raw = self.rfile.readline(MAX_BROKER_REQUEST_BYTES + 1)
                try:
                    self.wfile.write(broker._handle_payload(raw))
                    self.wfile.flush()
                except OSError:
                    pass

        self._server = _BrokerServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="PluginCapabilityBroker",
            daemon=True,
        )
        self._thread.start()
        host, port = self._server.server_address
        return {"host": host, "port": int(port), "token": self._token}

    def start_named_pipe(self, appcontainer_sid: str) -> dict[str, Any]:
        if self._server is not None or self._pipe_server is not None:
            raise RuntimeError("broker has already been started")
        pipe_name = r"\\.\pipe\VoiceAssistantPluginBroker-" + secrets.token_hex(16)
        self._pipe_server = WindowsNamedPipeServer(pipe_name, appcontainer_sid, self._handle_payload)
        self._pipe_server.start()
        return {"pipe": pipe_name, "token": self._token}

    def _dispatch_read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation not in {"filesystem.read_text", "filesystem.read_bytes"}:
            raise ValueError("operation has no broker handler")
        path = Path(str(arguments.get("path") or "")).resolve(strict=True)
        if not path.is_file():
            raise ValueError("read target is not a regular file")
        with path.open("rb") as stream:
            content = stream.read(MAX_BROKER_FILE_BYTES + 1)
        if len(content) > MAX_BROKER_FILE_BYTES:
            raise ValueError("file exceeds broker read limit")
        if operation == "filesystem.read_bytes":
            return {"data_base64": base64.b64encode(content).decode("ascii"), "size_bytes": len(content)}
        return {"text": content.decode("utf-8"), "size_bytes": len(content)}

    def close(self) -> None:
        server, pipe_server, thread = self._server, self._pipe_server, self._thread
        self._server = None
        self._pipe_server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if pipe_server is not None:
            pipe_server.close()
        if thread is not None:
            thread.join(timeout=2)
