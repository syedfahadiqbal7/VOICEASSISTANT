# mobile_api/server.py
"""
FastAPI + WebSocket server for Mobile App.
Port 8765 pe chalega.
"""

import asyncio
import hmac
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from mobile_api.connection_manager import manager
from mobile_api.bridge import bridge

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
        "connections": len(manager.active_connections)
    }


@app.get("/health")
async def health():
    return {"ok": True, "clients": len(manager.active_connections)}


def _expected_mobile_token() -> str:
    return os.environ.get("VOICEASSISTANT_MOBILE_TOKEN", "").strip()


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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    expected = _expected_mobile_token()
    supplied = websocket.headers.get("x-voiceassistant-token", "")
    if expected and not hmac.compare_digest(supplied, expected):
        await websocket.close(code=1008, reason="Mobile authentication required")
        return
    if not expected and os.environ.get("VOICEASSISTANT_MOBILE_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
        await websocket.close(code=1011, reason="Remote mobile access requires VOICEASSISTANT_MOBILE_TOKEN")
        return

    conn_id = await manager.connect(websocket)

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
        print(f"[MobileAPI] initial session_state: {e}")

    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                await manager.send_personal(websocket, {"type": "error", "message": "Message must be a JSON object"})
                continue
            msg_type = data.get("type")

            if msg_type == "user_message":
                try:
                    text = _validate_user_message(data)
                except ValueError as e:
                    await manager.send_personal(websocket, {"type": "error", "message": str(e)})
                    continue
                if text:
                    print(f"[MobileAPI] Received mobile message ({len(text)} chars)")
                    bridge.process_mobile_message(text)

            elif msg_type == "approval_response":
                try:
                    request_id, approved = _validate_approval_response(data)
                except ValueError as e:
                    await manager.send_personal(websocket, {"type": "error", "message": str(e)})
                    continue
                bridge.resolve_approval(request_id, approved, client_id=conn_id)

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

            elif msg_type == "cancel_schedule":
                job_id = data.get("job_id") or data.get("id")
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
        print(f"[MobileAPI] WebSocket error: {e}")
        manager.disconnect(websocket)


def start_server(host: str = "127.0.0.1", port: int = 8765):
    token = _expected_mobile_token()
    if host not in ("127.0.0.1", "localhost", "::1") and len(token) < 32:
        raise RuntimeError(
            "Remote mobile access refused: set VOICEASSISTANT_MOBILE_TOKEN to a random secret "
            "of at least 32 characters before binding beyond localhost."
        )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bridge.set_event_loop(loop)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
        ws_max_size=1_048_576,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    server = uvicorn.Server(config)

    print(f"[MobileAPI] Starting server on http://{host}:{port}")
    print(f"[MobileAPI] WebSocket endpoint: ws://{host}:{port}/ws")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("[MobileAPI] Remote access enabled with token authentication")
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    start_server()
