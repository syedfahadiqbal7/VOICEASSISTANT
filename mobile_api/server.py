# mobile_api/server.py
"""
FastAPI + WebSocket server for Mobile App.
Port 8765 pe chalega.
"""

import asyncio
import hmac
import logging
import os
import threading
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from mobile_api.connection_manager import manager
from mobile_api.bridge import bridge
from mobile_api.device_registry import registry as mobile_devices
from mobile_api.authorization import is_allowed
from mobile_api.rate_limiter import DeviceRateLimiter

logger = logging.getLogger("voiceassistant.mobile_api")
_server_lock = threading.Lock()
_server_stop_requested = threading.Event()
_active_server = None
_active_server_loop = None
manager.on_disconnect = bridge.client_disconnected
mobile_rate_limiter = DeviceRateLimiter()

app = FastAPI(title="VoiceAssistant Mobile API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "VoiceAssistant Mobile API",
        "connections": manager.connection_count()
    }


@app.get("/health")
async def health():
    return {"ok": True, "clients": manager.connection_count()}


def _max_mobile_connections() -> int:
    raw = os.environ.get("VOICEASSISTANT_MOBILE_MAX_CONNECTIONS", "5").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("VOICEASSISTANT_MOBILE_MAX_CONNECTIONS must be an integer from 1 to 32") from error
    if not 1 <= value <= 32:
        raise RuntimeError("VOICEASSISTANT_MOBILE_MAX_CONNECTIONS must be between 1 and 32")
    return value


def _expected_mobile_token() -> str:
    return os.environ.get("VOICEASSISTANT_MOBILE_TOKEN", "").strip()


def _mobile_principal(headers):
    """Authenticate and resolve the server-owned role for this connection."""
    if mobile_devices.is_configured():
        return mobile_devices.authenticate_principal(
            headers.get("x-voiceassistant-device-id", ""),
            headers.get("x-voiceassistant-device-token", ""),
        )

    expected = _expected_mobile_token()
    supplied = headers.get("x-voiceassistant-token", "")
    if expected:
        if hmac.compare_digest(supplied, expected):
            return {"device_id": "legacy-shared-token", "name": "Legacy shared-token client", "role": "operator"}
        return None

    # No credentials are accepted only for an explicitly loopback-only listener.
    if os.environ.get("VOICEASSISTANT_MOBILE_HOST", "127.0.0.1") in ("127.0.0.1", "localhost", "::1"):
        return {"device_id": "loopback-local", "name": "Loopback local client", "role": "operator"}
    return None


def _authenticate_mobile_headers(headers) -> bool:
    """Compatibility helper retained for existing authentication tests."""
    return _mobile_principal(headers) is not None


def _reauthenticate_mobile_principal(websocket):
    """Re-resolve role and credentials so revocation/role changes take effect."""
    try:
        return _mobile_principal(websocket.headers)
    except Exception:
        logger.exception("Mobile device revalidation failed", extra={"event": "mobile.device_revalidation_failed"})
        return None


def _reauthenticate_mobile(websocket) -> bool:
    return _reauthenticate_mobile_principal(websocket) is not None


def _validate_user_message(data: dict) -> str:
    text = data.get("text", "")
    if not isinstance(text, str):
        raise ValueError("Message text must be a string")
    text = text.strip()
    if len(text) > 10000:
        raise ValueError("Message exceeds the 10,000 character limit")
    return text


def _validate_approval_response(data: dict) -> tuple[str, bool]:
    request_id = data.get("request_id")
    approved = data.get("approved", False)
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise ValueError("Invalid approval request_id")
    if not isinstance(approved, bool):
        raise ValueError("Approval value must be a JSON boolean")
    return request_id, approved


async def _resolve_mobile_approval(websocket, request_id: str, approved: bool, conn_id: str) -> bool:
    accepted = bridge.resolve_approval(request_id, approved, client_id=conn_id)
    await manager.send_personal(websocket, {
        "type": "approval_ack",
        "request_id": request_id,
        "accepted": accepted,
    })
    return accepted


def _validate_job_id(data: dict) -> str:
    job_id = data.get("job_id", data.get("id"))
    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
        raise ValueError("Invalid schedule job_id")
    return job_id.strip()


def _tls_options_from_env() -> dict:
    certfile = os.environ.get("VOICEASSISTANT_TLS_CERTFILE", "").strip()
    keyfile = os.environ.get("VOICEASSISTANT_TLS_KEYFILE", "").strip()
    if bool(certfile) != bool(keyfile):
        raise RuntimeError("Configure both VOICEASSISTANT_TLS_CERTFILE and VOICEASSISTANT_TLS_KEYFILE")
    if not certfile:
        return {}
    if not Path(certfile).is_file() or not Path(keyfile).is_file():
        raise RuntimeError("Configured TLS certificate or private key file does not exist")
    return {"ssl_certfile": certfile, "ssl_keyfile": keyfile}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        principal = _mobile_principal(websocket.headers)
    except Exception:
        # A corrupt/unreadable encrypted registry is a hard authentication failure.
        logger.exception("Mobile device registry could not be read", extra={"event": "mobile.device_registry_failed"})
        await websocket.close(code=1011, reason="Mobile authentication is unavailable")
        return
    if principal is None:
        await websocket.close(code=1008, reason="Mobile authentication required")
        return

    try:
        max_connections = _max_mobile_connections()
    except RuntimeError:
        logger.exception("Invalid mobile connection limit configuration", extra={"event": "mobile.connection_limit_invalid"})
        await websocket.close(code=1011, reason="Invalid server connection limit configuration")
        return

    conn_id = await manager.connect(websocket, max_connections=max_connections)
    if conn_id is None:
        await websocket.close(code=1013, reason="Mobile connection capacity reached")
        return
    manager.set_role(websocket, principal["role"])

    await manager.send_personal(websocket, {
        "type": "connected",
        "message": "Connected to VoiceAssistant",
        "conn_id": conn_id
    })

    # Push current session_state on connect
    try:
        state = bridge.get_session_state()
        if state:
            await manager.send_personal(websocket, {
                "type": "session_state",
                "state": state
            })
    except Exception as e:
        logger.exception("Initial mobile session-state delivery failed", extra={"event": "mobile.session_state_failed"})

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            except asyncio.TimeoutError:
                refreshed = _reauthenticate_mobile_principal(websocket)
                if refreshed is None:
                    await websocket.close(code=1008, reason="Mobile device is no longer authorized")
                    manager.disconnect(websocket)
                    break
                principal = refreshed
                manager.set_role(websocket, principal["role"])
                continue
            refreshed = _reauthenticate_mobile_principal(websocket)
            if refreshed is None:
                await websocket.close(code=1008, reason="Mobile device is no longer authorized")
                manager.disconnect(websocket)
                break
            principal = refreshed
            manager.set_role(websocket, principal["role"])
            if not isinstance(data, dict):
                await manager.send_personal(websocket, {"type": "error", "message": "Message must be a JSON object"})
                continue
            msg_type = data.get("type")
            if not is_allowed(principal["role"], msg_type):
                logger.warning(
                    "Denied mobile command %r for role %s",
                    msg_type,
                    principal["role"],
                    extra={"event": "mobile.authorization_denied"},
                )
                await manager.send_personal(websocket, {
                    "type": "error",
                    "message": "This device is not authorized to perform that operation.",
                })
                continue
            rate_decision = mobile_rate_limiter.consume(principal["device_id"], str(msg_type or ""))
            if not rate_decision.allowed:
                await manager.send_personal(websocket, {
                    "type": "error",
                    "message": "Rate limit exceeded; slow down and retry."
                })
                if rate_decision.close_connection:
                    await websocket.close(code=1013, reason="Device message rate limit exceeded")
                    manager.disconnect(websocket)
                    break
                continue

            if msg_type == "user_message":
                try:
                    text = _validate_user_message(data)
                except ValueError as e:
                    await manager.send_personal(websocket, {"type": "error", "message": str(e)})
                    continue
                if text:
                    logger.info("Received mobile message (%s characters)", len(text), extra={"event": "mobile.message_received"})
                    bridge.process_mobile_message(text)

            elif msg_type == "approval_response":
                try:
                    request_id, approved = _validate_approval_response(data)
                except ValueError as e:
                    await manager.send_personal(websocket, {"type": "error", "message": str(e)})
                    continue
                await _resolve_mobile_approval(websocket, request_id, approved, conn_id)

            elif msg_type == "get_session_state":
                state = bridge.get_session_state()
                await manager.send_personal(websocket, {
                    "type": "session_state",
                    "state": state
                })

            elif msg_type == "list_schedules":
                jobs = bridge.list_schedules()
                await manager.send_personal(websocket, {
                    "type": "schedules",
                    "jobs": jobs
                })

            elif msg_type == "list_schedule_history":
                records = bridge.list_schedule_history(limit=50)
                await manager.send_personal(websocket, {
                    "type": "schedule_history",
                    "records": records,
                })

            elif msg_type == "cancel_schedule":
                try:
                    job_id = _validate_job_id(data)
                except ValueError as error:
                    await manager.send_personal(websocket, {"type": "error", "message": str(error)})
                    continue
                ok = bridge.cancel_schedule(job_id)
                await manager.send_personal(websocket, {
                    "type": "schedule_cancelled",
                    "job_id": job_id,
                    "ok": ok
                })
                # refresh list
                jobs = bridge.list_schedules()
                await manager.send_personal(websocket, {
                    "type": "schedules",
                    "jobs": jobs
                })

            elif msg_type == "undo_last":
                try:
                    result = bridge.undo_last()
                except Exception as error:
                    logger.exception("Mobile undo request failed", extra={"event": "mobile.undo_failed"})
                    result = {"ok": False, "message": "Undo could not be completed"}
                await manager.send_personal(websocket, {"type": "undo_result", "result": result})
                await manager.send_personal(websocket, {
                    "type": "session_state",
                    "state": bridge.get_session_state(),
                })

            elif msg_type == "ping":
                await manager.send_personal(websocket, {"type": "pong"})

            else:
                await manager.send_personal(websocket, {
                    "type": "error",
                    "message": f"Unknown type: {msg_type}"
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.exception("Mobile WebSocket terminated unexpectedly", extra={"event": "mobile.websocket_failed"})
        manager.disconnect(websocket)


def stop_server() -> bool:
    """Request graceful shutdown, including when startup is still racing."""
    _server_stop_requested.set()
    with _server_lock:
        server = _active_server
        loop = _active_server_loop
    if server is None or loop is None or loop.is_closed():
        return False
    try:
        loop.call_soon_threadsafe(setattr, server, "should_exit", True)
        return True
    except RuntimeError:
        return False


def start_server(host: str = "127.0.0.1", port: int = 8765):
    if _server_stop_requested.is_set():
        logger.info("Mobile API startup skipped because shutdown was already requested", extra={"event": "mobile.startup_cancelled"})
        return
    token = _expected_mobile_token()
    _max_mobile_connections()  # Fail fast on invalid connection-cap configuration.
    if (
        host not in ("127.0.0.1", "localhost", "::1")
        and not mobile_devices.is_configured()
        and len(token) < 32
    ):
        raise RuntimeError(
            "Remote mobile access refused: enroll at least one device with "
            "mobile_device_cli.py or configure VOICEASSISTANT_MOBILE_TOKEN "
            "with a random secret of at least 32 characters."
        )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bridge.set_event_loop(loop)

    tls_options = _tls_options_from_env()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
        ws_max_size=1_048_576,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_graceful_shutdown=2,
        **tls_options,
    )
    server = uvicorn.Server(config)

    global _active_server, _active_server_loop
    with _server_lock:
        _active_server = server
        _active_server_loop = loop
        if _server_stop_requested.is_set():
            server.should_exit = True

    scheme = "wss" if tls_options else "ws"
    http_scheme = "https" if tls_options else "http"
    logger.info("Starting mobile API on %s://%s:%s", http_scheme, host, port, extra={"event": "mobile.server_starting"})
    logger.info("Mobile WebSocket endpoint: %s://%s:%s/ws", scheme, host, port, extra={"event": "mobile.websocket_endpoint"})
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.info("Remote access enabled with per-device or legacy token authentication", extra={"event": "mobile.remote_access_enabled"})
    try:
        loop.run_until_complete(server.serve())
    finally:
        with _server_lock:
            if _active_server is server:
                _active_server = None
                _active_server_loop = None
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


if __name__ == "__main__":
    start_server()
