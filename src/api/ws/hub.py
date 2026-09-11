from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..schemas.ws import ServerMessage, ServerMessageType


@dataclass(eq=False)
class Subscription:
    task_id: str
    queue: asyncio.Queue[ServerMessage] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )


class WsHub:
    """管理 WebSocket 訂閱與廣播。每個連線一個有界佇列（maxsize=1000）。"""

    def __init__(self) -> None:
        self._rooms: dict[str, set[Subscription]] = {}

    async def subscribe(self, task_id: str) -> Subscription:
        sub = Subscription(task_id=task_id)
        self._rooms.setdefault(task_id, set()).add(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        room = self._rooms.get(sub.task_id)
        if room is not None:
            room.discard(sub)
            if not room:
                self._rooms.pop(sub.task_id, None)

    async def broadcast(self, task_id: str, message: ServerMessage) -> None:
        room = self._rooms.get(task_id)
        if not room:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        for sub in list(room):
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                lag_msg = ServerMessage(
                    type=ServerMessageType.ERROR,
                    task_id=task_id,
                    experiment_id=message.experiment_id,
                    timestamp=now_iso,
                    payload={"reason": "subscriber_lagging"},
                )
                try:
                    sub.queue.put_nowait(lag_msg)
                except asyncio.QueueFull:
                    pass
                try:
                    sub.queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass

    def room_size(self, task_id: str) -> int:
        return len(self._rooms.get(task_id, set()))
