"""WebSocket client registry.

One connection per page. A phone is `player` (display + camera), the
laptop is `controller` (driver + scoreboard), and a spectator is read-only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Client:
    id: str
    ws: Any
    role: str = "controller"
    player_id: str | None = None
    room: str | None = None
    camera_ready: bool = False
    connected_at: float = field(default_factory=time.time)
    last_frame_at: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "player_id": self.player_id,
            "room": self.room,
            "camera_ready": self.camera_ready,
        }


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: dict[str, Client] = {}

    # -- registry ------------------------------------------------------
    def add(self, client: Client) -> None:
        self.clients[client.id] = client

    def remove(self, client_id: str) -> None:
        self.clients.pop(client_id, None)

    def get(self, client_id: str) -> Client | None:
        return self.clients.get(client_id)

    def player_client(self, player_id: str | None) -> Client | None:
        if not player_id:
            return None
        candidates = [
            c for c in self.clients.values()
            if c.role == "player" and c.player_id == player_id
        ]
        candidates.sort(key=lambda c: (not c.camera_ready, -c.connected_at))
        return candidates[0] if candidates else None

    def controller(self) -> Client | None:
        controllers = [c for c in self.clients.values() if c.role == "controller"]
        controllers.sort(key=lambda c: c.connected_at)
        return controllers[0] if controllers else None

    def in_room(self, code: str) -> list[Client]:
        return [c for c in self.clients.values() if c.room == code]

    def snapshot(self) -> list[dict[str, Any]]:
        return [c.public() for c in self.clients.values()]

    # -- messaging -----------------------------------------------------
    async def send(self, client_id: str, message: dict[str, Any]) -> bool:
        client = self.clients.get(client_id)
        if not client:
            return False
        try:
            await client.ws.send_json(message)
            return True
        except Exception:
            self.remove(client_id)
            return False

    async def send_to_player(self, player_id: str, message: dict[str, Any]) -> bool:
        client = self.player_client(player_id)
        if not client:
            return False
        return await self.send(client.id, message)

    async def broadcast(self, message: dict[str, Any], *,
                        room: str | None = None,
                        role: str | None = None,
                        exclude: str | None = None) -> int:
        sent = 0
        dead: list[str] = []
        for client in list(self.clients.values()):
            if exclude and client.id == exclude:
                continue
            if room is not None and client.room != room:
                continue
            if role is not None and client.role != role:
                continue
            try:
                await client.ws.send_json(message)
                sent += 1
            except Exception:
                dead.append(client.id)
        for cid in dead:
            self.remove(cid)
        return sent
