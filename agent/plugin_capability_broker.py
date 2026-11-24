"""Host-owned, per-invocation plugin capability broker.

The loopback IPC channel is a migration interface, not an OS sandbox. Until a
restricted Windows token/AppContainer prevents direct access, same-user plugin
code can bypass this broker and call OS APIs directly.
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
        self._thread = None

    def start(self) -> dict[str, Any]:
        if self._server is not None:
            raise RuntimeError("broker has already been started")
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(5)
                raw = self.rfile.readline(MAX_BROKER_REQUEST_BYTES + 1)
                if not raw or len(raw) > MAX_BROKER_REQUEST_BYTES or not raw.endswith(b"\n"):
                    self._reply({"ok": False, "error_code": "invalid_request"})
                    return
                try:
                    request = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._reply({"ok": False, "error_code": "invalid_request"})
                    return
                if not isinstance(request, dict):
                    self._reply({"ok": False, "error_code": "invalid_request"})
                    return
                allowed_fields = {
                    "protocol_version", "request_id", "token",
                    "capability", "operation", "arguments",
                }
                if set(request) != allowed_fields or request.get("protocol_version") != 1:
                    self._reply({"ok": False, "error_code": "invalid_request"})
                    return
                request_id = request.get("request_id")
                if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                    self._reply({"ok": False, "error_code": "invalid_request"})
                    return
                supplied_token = request.get("token")
                if not isinstance(supplied_token, str) or not hmac.compare_digest(supplied_token, broker._token):
                    self._reply({"ok": False, "request_id": request_id, "error_code": "unauthorized"})
                    return
                arguments = request.get("arguments")
                if not isinstance(arguments, dict):
                    self._reply({"ok": False, "request_id": request_id, "error_code": "invalid_arguments"})
                    return

                capability = str(request.get("capability") or "")
                operation = str(request.get("operation") or "")
                decision = broker.policy.evaluate_capability_request(
                    broker.tool_name,
                    capability,
                    operation,
                    arguments,
                    declared_permissions=broker.declared_permissions,
                    approved=False,  # Approval is host-owned; never accepted from plugin IPC.
                )
                if not decision.get("allowed"):
                    self._reply({
                        "ok": False,
                        "request_id": request_id,
                        "error_code": "capability_denied",
                        "reason": str(decision.get("reason") or "denied"),
                    })
                    return

                try:
                    result = broker._dispatch_read(operation, arguments)
                    self._reply({"ok": True, "request_id": request_id, "result": result})
                except FileNotFoundError:
                    self._reply({"ok": False, "request_id": request_id, "error_code": "not_found"})
                except (OSError, UnicodeError, ValueError) as error:
                    self._reply({
                        "ok": False,
                        "request_id": request_id,
                        "error_code": "broker_operation_failed",
                        "reason": type(error).__name__,
                    })

            def _reply(self, payload: dict[str, Any]):
                try:
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
                except (TypeError, ValueError):
                    encoded = b'{"ok":false,"error_code":"invalid_response"}\n'
                if len(encoded) > MAX_BROKER_RESPONSE_BYTES:
                    encoded = b'{"ok":false,"error_code":"response_too_large"}\n'
                self.wfile.write(encoded)
                self.wfile.flush()

        self._server = _BrokerServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="PluginCapabilityBroker",
            daemon=True,
        )
        self._thread.start()
        host, port = self._server.server_address
        return {"host": host, "port": int(port), "token": self._token}

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
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)
