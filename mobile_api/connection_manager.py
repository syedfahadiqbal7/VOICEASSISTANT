# mobile_api/connection_manager.py
"""
WebSocket connection manager.
Har connected mobile phone ka record rakhta hai aur unko messages broadcast karta hai.
"""

from typing import List, Dict, Any
from fastapi import WebSocket
import json
import asyncio
import uuid


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.connection_ids: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        conn_id = str(uuid.uuid4())[:8]
        self.active_connections.append(websocket)
        self.connection_ids[websocket] = conn_id
        print(f"[MobileAPI] New connection: {conn_id} | Total: {len(self.active_connections)}")
        return conn_id

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        conn_id = self.connection_ids.pop(websocket, "unknown")
        print(f"[MobileAPI] Disconnected: {conn_id} | Remaining: {len(self.active_connections)}")

    async def send_personal(self, websocket: WebSocket, data: dict):
        try:
            await websocket.send_text(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            print(f"[MobileAPI] send_personal error: {e}")
            self.disconnect(websocket)

    async def broadcast(self, data: dict):
        """Saare connected phones ko message bhejo."""
        if not self.active_connections:
            return

        message = json.dumps(data, ensure_ascii=False)
        dead = []

        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)

        for conn in dead:
            self.disconnect(conn)

    def has_connections(self) -> bool:
        return len(self.active_connections) > 0


# Global instance
manager = ConnectionManager()