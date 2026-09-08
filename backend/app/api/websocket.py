import asyncio

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


class ConnectionManager:
    def __init__(self):
        self.connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        await websocket.send_json({"type": "connected", "data": {}})

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        if not self.connections:
            return
        results = await asyncio.gather(
            *(connection.send_json(message) for connection in tuple(self.connections)),
            return_exceptions=True,
        )
        for connection, result in zip(tuple(self.connections), results):
            if isinstance(result, Exception):
                self.disconnect(connection)


async def serve_market_socket(websocket: WebSocket, manager: ConnectionManager) -> None:
    await manager.connect(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message.lower() == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
