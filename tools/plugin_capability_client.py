"""Client for the host-owned VoiceAssistant plugin capability broker."""
from __future__ import annotations

import json
import os
import secrets
import socket
from typing import Any


MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


class PluginCapabilityError(RuntimeError):
    pass


def request_capability(operation: str, capability: str, arguments: dict[str, Any]) -> dict[str, Any]:
    host = os.environ.get("VOICEASSISTANT_BROKER_HOST")
    port_text = os.environ.get("VOICEASSISTANT_BROKER_PORT")
    pipe_name = os.environ.get("VOICEASSISTANT_BROKER_PIPE")
    token = os.environ.get("VOICEASSISTANT_BROKER_TOKEN")
    if not token or (not pipe_name and (not host or not port_text)):
        raise PluginCapabilityError("Host capability broker is unavailable")
    try:
        port = int(port_text) if port_text else 0
    except ValueError as error:
        raise PluginCapabilityError("Host capability broker configuration is invalid") from error

    request_id = secrets.token_hex(16)
    request = {
        "protocol_version": 1,
        "request_id": request_id,
        "token": token,
        "capability": str(capability),
        "operation": str(operation),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }
    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise PluginCapabilityError("Capability request exceeds the size limit")

    try:
        if pipe_name:
            from security.windows_named_pipe import send_named_pipe_message
            response = bytearray(send_named_pipe_message(pipe_name, payload, timeout_ms=5000))
        else:
            with socket.create_connection((host, port), timeout=5) as connection:
                connection.settimeout(5)
                connection.sendall(payload)
                response = bytearray()
                while len(response) <= MAX_RESPONSE_BYTES:
                    chunk = connection.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(response)))
                    if not chunk:
                        break
                    response.extend(chunk)
                    if b"\n" in chunk:
                        break
    except (OSError, RuntimeError) as error:
        raise PluginCapabilityError("Host capability broker connection failed") from error

    if len(response) > MAX_RESPONSE_BYTES or not response.endswith(b"\n"):
        raise PluginCapabilityError("Host capability broker returned an invalid response")
    try:
        result = json.loads(bytes(response).split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PluginCapabilityError("Host capability broker returned malformed JSON") from error
    if not isinstance(result, dict) or result.get("request_id") != request_id:
        raise PluginCapabilityError("Host capability broker response did not match the request")
    if result.get("ok") is not True:
        code = str(result.get("error_code") or "broker_denied")
        reason = str(result.get("reason") or code)
        raise PluginCapabilityError(f"{code}: {reason}")
    value = result.get("result")
    if not isinstance(value, dict):
        raise PluginCapabilityError("Host capability broker returned an invalid result")
    return value


def read_text(path: str) -> str:
    return str(request_capability(
        "filesystem.read_text", "filesystem:read", {"path": str(path)}
    )["text"])


def read_bytes(path: str) -> bytes:
    import base64

    result = request_capability("filesystem.read_bytes", "filesystem:read", {"path": str(path)})
    return base64.b64decode(str(result["data_base64"]), validate=True)
