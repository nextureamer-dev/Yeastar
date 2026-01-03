import json
import asyncio
import logging
from typing import Dict, Set, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        # Extension -> Set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # All connected clients (for broadcast)
        self.all_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, extension: str = None):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.all_connections.add(websocket)

        if extension:
            if extension not in self.active_connections:
                self.active_connections[extension] = set()
            self.active_connections[extension].add(websocket)

        logger.info(f"WebSocket connected. Extension: {extension}. Total: {len(self.all_connections)}")

    def disconnect(self, websocket: WebSocket, extension: str = None):
        """Remove a WebSocket connection."""
        self.all_connections.discard(websocket)

        if extension and extension in self.active_connections:
            self.active_connections[extension].discard(websocket)
            if not self.active_connections[extension]:
                del self.active_connections[extension]

        logger.info(f"WebSocket disconnected. Total: {len(self.all_connections)}")

    async def send_to_extension(self, extension: str, data: Dict[str, Any]):
        """Send data to all connections for a specific extension."""
        if extension not in self.active_connections:
            return

        message = json.dumps(data)
        dead_connections = set()

        for connection in self.active_connections[extension]:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error sending to extension {extension}: {e}")
                dead_connections.add(connection)

        # Clean up dead connections
        for conn in dead_connections:
            self.disconnect(conn, extension)

    async def broadcast(self, data: Dict[str, Any]):
        """Broadcast data to all connected clients."""
        message = json.dumps(data)
        dead_connections = set()

        for connection in self.all_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error broadcasting: {e}")
                dead_connections.add(connection)

        # Clean up dead connections
        for conn in dead_connections:
            self.all_connections.discard(conn)

    async def send_call_popup(self, extension: str, caller: str, contact: Dict = None):
        """Send call popup notification to extension."""
        await self.send_to_extension(extension, {
            "type": "call_popup",
            "caller": caller,
            "contact": contact,
        })

    async def send_call_ended(self, extension: str):
        """Send call ended notification."""
        await self.send_to_extension(extension, {
            "type": "call_ended",
        })

    async def send_extension_status(self, extension: str, status: str):
        """Broadcast extension status change."""
        await self.broadcast({
            "type": "extension_status",
            "extension": extension,
            "status": status,
        })

    async def send_summary_processed(self, call_id: str, summary_data: Dict[str, Any] = None):
        """Broadcast when a new call summary is processed."""
        await self.broadcast({
            "type": "summary_processed",
            "call_id": call_id,
            "summary": summary_data,
        })

    async def send_analytics_update(self):
        """Broadcast analytics update signal (triggers dashboard refresh)."""
        await self.broadcast({
            "type": "analytics_update",
        })


# Global WebSocket manager instance
websocket_manager = WebSocketManager()


def get_websocket_manager() -> WebSocketManager:
    return websocket_manager
