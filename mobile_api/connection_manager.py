# mobile_api/connection_manager.py
"""
WebSocket connection manager.
Har connected mobile phone ka record rakhta hai aur unko messages broadcast karta hai.
"""

from typing import List, Dict, Any, Callable, Optional
import threading
import logging
from fastapi import WebSocket
import json
import asyncio
import uuid

logger = logging.getLogger("voiceassistant.mobile_connections")


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.connection_ids: Dict[WebSocket, str] = {}
        self._lock = threading.RLock()
        self._pending_connections = 0
        self.on_disconnect: Optional[Callable[[str], None]] = None

    async def connect(self, websocket: WebSocket, max_connections: Optional[int] = None) -> Optional[str]:
        # Reserve a slot before awaiting accept() so concurrent handshakes cannot
        # race past the configured connection ceiling.
        with self._lock:
            if max_connections is not None and (
                len(self.active_connections) + self._pending_connections >= max_connections
            ):
                return None
            self._pending_connections += 1
        try:
            await websocket.accept()
        except BaseException:
            with self._lock:
                self._pending_connections = max(0, self._pending_connections - 1)
            raise
        conn_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._pending_connections = max(0, self._pending_connections - 1)
            self.active_connections.append(websocket)
            self.connection_ids[websocket] = conn_id
            total = len(self.active_connections)
        logger.info("Mobile client connected (%s active)", total, extra={"event": "mobile.connection_opened"})
        return conn_id

    def disconnect(self, websocket: WebSocket):
        with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            conn_id = self.connection_ids.pop(websocket, "unknown")
            remaining = len(self.active_connections)
        logger.info("Mobile client disconnected (%s remaining)", remaining, extra={"event": "mobile.connection_closed"})
        if self.on_disconnect and conn_id != "unknown":
            try:
                self.on_disconnect(conn_id)
            except Exception as error:
                logger.exception("Mobile disconnect callback failed", extra={"event": "mobile.disconnect_callback_failed"})

    def first_connection(self):
        with self._lock:
            if not self.active_connections:
                return None, None
            websocket = self.active_connections[0]
            return websocket, self.connection_ids.get(websocket)

    def connection_count(self) -> int:
        with self._lock:
            return len(self.active_connections)

    async def send_personal(self, websocket: WebSocket, data: dict):
        try:
            await websocket.send_text(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.exception("Mobile personal message delivery failed", extra={"event": "mobile.send_personal_failed"})
            self.disconnect(websocket)

    async def broadcast(self, data: dict):
        """Saare connected phones ko message bhejo."""
        with self._lock:
            connections = list(self.active_connections)
        if not connections:
            return

        message = json.dumps(data, ensure_ascii=False)
        dead = []

        for connection in connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)

        for conn in dead:
            self.disconnect(conn)

    def has_connections(self) -> bool:
        return self.connection_count() > 0


# Global instance
manager = ConnectionManager()