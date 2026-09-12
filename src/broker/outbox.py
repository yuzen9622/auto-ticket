"""遙測 outbox：Worker 寫入、API Server 尾隨游標讀出並扇出到 WebSocket。

`OutboxWriter.publish` **必須**同步且完全不碰 event loop：截圖 hook 與 FSM callback
都可能在非 loop 執行緒被呼叫（`src/browser/manager.py:256-267` 的執行緒契約）。
`queue.SimpleQueue` 為無界、thread-safe，符合此契約。
"""

from __future__ import annotations

import asyncio
import queue
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select

from .models import OutboxEventModel


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: int
    task_id: str
    experiment_id: str | None
    type: str
    payload: dict[str, Any]
    created_at: datetime
    ephemeral: bool


@dataclass(frozen=True, slots=True)
class _PendingEvent:
    task_id: str
    experiment_id: str | None
    type: str
    payload: dict[str, Any]
    created_at: datetime
    ephemeral: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_record(orm: OutboxEventModel) -> OutboxRecord:
    return OutboxRecord(
        id=int(orm.id),
        task_id=orm.task_id,
        experiment_id=orm.experiment_id,
        type=orm.type,
        payload=dict(orm.payload or {}),
        created_at=orm.created_at,
        ephemeral=bool(orm.ephemeral),
    )


class OutboxWriter:
    """thread-safe 入列 + asyncio 批次落地。"""

    def __init__(self, db: Any, *, flush_ms: int = 100) -> None:
        self._db = db
        self._flush_s = max(0.001, flush_ms / 1000.0)
        self._queue: queue.SimpleQueue[_PendingEvent] = queue.SimpleQueue()
        self._closed = False

    def publish(
        self,
        *,
        task_id: str,
        type: str,
        payload: dict[str, Any],
        experiment_id: str | None = None,
        ephemeral: bool = False,
    ) -> None:
        """同步、非阻塞、可由任意執行緒呼叫。"""
        if self._closed:
            return
        self._queue.put_nowait(
            _PendingEvent(
                task_id=task_id,
                experiment_id=experiment_id,
                type=type,
                payload=dict(payload),
                created_at=_utcnow(),
                ephemeral=ephemeral,
            )
        )

    def _drain(self) -> list[_PendingEvent]:
        batch: list[_PendingEvent] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                return batch

    async def flush(self) -> int:
        batch = self._drain()
        if not batch:
            return 0
        async with self._db.session() as session:
            session.add_all(
                [
                    OutboxEventModel(
                        task_id=item.task_id,
                        experiment_id=item.experiment_id,
                        type=item.type,
                        payload=item.payload,
                        created_at=item.created_at,
                        ephemeral=item.ephemeral,
                    )
                    for item in batch
                ]
            )
            await session.flush()
        return len(batch)

    async def run(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._flush_s)
            await self.flush()

    async def aclose(self) -> None:
        """排空後才返回：未落地的 frame 等於前端永遠看不到的事實。"""
        self._closed = True
        while await self.flush():
            pass


class OutboxReader:
    def __init__(self, db: Any, *, start_at_end: bool = True) -> None:
        self._db = db
        self._start_at_end = start_at_end
        self._cursor = 0

    @property
    def cursor(self) -> int:
        return self._cursor

    async def initialise(self) -> None:
        if not self._start_at_end:
            self._cursor = 0
            return
        async with self._db.session() as session:
            max_id = (
                await session.execute(select(func.max(OutboxEventModel.id)))
            ).scalar()
        self._cursor = int(max_id or 0)

    async def poll(self, *, limit: int = 500) -> list[OutboxRecord]:
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.id > self._cursor)
            .order_by(OutboxEventModel.id)
            .limit(limit)
        )
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            records = [_to_record(orm) for orm in rows]
        if records:
            self._cursor = records[-1].id
        return records

    async def replay(self, task_id: str, *, limit: int = 50) -> list[OutboxRecord]:
        """取該 task 最近 `limit` 筆歷史，依 id 遞增回傳。"""
        stmt = (
            select(OutboxEventModel)
            .where(OutboxEventModel.task_id == task_id)
            .order_by(OutboxEventModel.id.desc())
            .limit(limit)
        )
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            records = [_to_record(orm) for orm in rows]
        records.reverse()
        return records

    async def prune_ephemeral(self, older_than_s: float = 60.0) -> int:
        cutoff = _utcnow() - timedelta(seconds=older_than_s)
        async with self._db.session() as session:
            result = await session.execute(
                delete(OutboxEventModel).where(
                    OutboxEventModel.ephemeral.is_(True),
                    OutboxEventModel.created_at < cutoff,
                )
            )
            return int(result.rowcount or 0)
