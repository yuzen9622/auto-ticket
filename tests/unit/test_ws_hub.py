from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from api.schemas.ws import ServerMessage, ServerMessageType
from api.ws.hub import WsHub
from tests.netguard import netguard_autouse  # noqa: F401


async def test_ws_hub_subscribe_and_broadcast() -> None:
    hub = WsHub()
    task_id = "task_hub_test"
    sub1 = await hub.subscribe(task_id)
    sub2 = await hub.subscribe(task_id)

    assert hub.room_size(task_id) == 2

    now = datetime.now(timezone.utc).isoformat()
    msg = ServerMessage(
        type=ServerMessageType.TASK_LOG,
        task_id=task_id,
        timestamp=now,
        payload={"message": "hello"},
    )
    await hub.broadcast(task_id, msg)

    got1 = await sub1.queue.get()
    got2 = await sub2.queue.get()
    assert got1.payload["message"] == "hello"
    assert got2.payload["message"] == "hello"

    await hub.unsubscribe(sub1)
    assert hub.room_size(task_id) == 1

    await hub.unsubscribe(sub2)
    assert hub.room_size(task_id) == 0


async def test_ws_hub_slow_client_drop_and_warn() -> None:
    hub = WsHub()
    task_id = "task_slow_client"
    sub = await hub.subscribe(task_id)
    # 縮小佇列以模擬塞滿
    sub.queue = asyncio.Queue(maxsize=2)

    now = datetime.now(timezone.utc).isoformat()
    msg1 = ServerMessage(
        type=ServerMessageType.TASK_LOG,
        task_id=task_id,
        timestamp=now,
        payload={"seq": 1},
    )
    msg2 = ServerMessage(
        type=ServerMessageType.TASK_LOG,
        task_id=task_id,
        timestamp=now,
        payload={"seq": 2},
    )
    msg3 = ServerMessage(
        type=ServerMessageType.TASK_LOG,
        task_id=task_id,
        timestamp=now,
        payload={"seq": 3},
    )

    await hub.broadcast(task_id, msg1)
    await hub.broadcast(task_id, msg2)
    # 第 3 筆觸發 QueueFull
    await hub.broadcast(task_id, msg3)

    # 驗證佇列中出現了 ERROR (subscriber_lagging)
    items = []
    while not sub.queue.empty():
        items.append(sub.queue.get_nowait())

    assert any(
        item.type == ServerMessageType.ERROR
        and item.payload.get("reason") == "subscriber_lagging"
        for item in items
    )
