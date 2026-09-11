from __future__ import annotations

import asyncio
import contextlib
import time

from broker.outbox import OutboxReader

from ..schemas.ws import ServerMessage, ServerMessageType
from .hub import WsHub


class OutboxPump:
    """背景任務輪詢 broker_outbox，推播至 WsHub，定期清理 ephemeral 事件。"""

    def __init__(
        self,
        reader: OutboxReader,
        hub: WsHub,
        *,
        poll_interval_s: float = 0.1,
        prune_interval_s: float = 60.0,
    ) -> None:
        self._reader = reader
        self._hub = hub
        self._poll_interval_s = float(poll_interval_s)
        self._prune_interval_s = float(prune_interval_s)
        self._running = False
        self._last_prune = 0.0

    async def run(self) -> None:
        self._running = True
        await self._reader.initialise()
        self._last_prune = time.monotonic()

        while self._running:
            try:
                records = await self._reader.poll(limit=500)
                for rec in records:
                    msg = ServerMessage(
                        type=ServerMessageType(rec.type),
                        task_id=rec.task_id,
                        experiment_id=rec.experiment_id,
                        timestamp=rec.created_at.isoformat(),
                        payload=dict(rec.payload),
                        outbox_id=rec.id,
                    )
                    await self._hub.broadcast(rec.task_id, msg)

                now = time.monotonic()
                if now - self._last_prune >= self._prune_interval_s:
                    with contextlib.suppress(Exception):
                        await self._reader.prune_ephemeral()
                    self._last_prune = now

            except asyncio.CancelledError:
                break
            except Exception:
                pass

            await asyncio.sleep(self._poll_interval_s)

    async def stop(self) -> None:
        self._running = False
