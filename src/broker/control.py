"""控制訊號通道：API Server 下令、Worker 收斂。

訊號一律落 DB（`control_signals`）。記憶體 broadcast 跨不了行程邊界，而
API Server 與 Worker 是兩個獨立行程。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select

from broker.models import ControlSignalModel


class ControlAction(str, Enum):
    EMERGENCY_STOP = "EMERGENCY_STOP"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    FORCE_TRANSITION = "FORCE_TRANSITION"


@dataclass(frozen=True, slots=True)
class ControlSignalRecord:
    id: int
    task_id: str
    action: ControlAction
    payload: dict[str, Any] | None
    created_at: datetime
    consumed_at: datetime | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def publish(
    db: Any,
    *,
    task_id: str,
    action: ControlAction,
    payload: dict[str, Any] | None = None,
) -> int:
    orm = ControlSignalModel(
        task_id=task_id,
        action=ControlAction(action).value,
        payload=dict(payload) if payload else None,
        created_at=_utcnow(),
    )
    async with db.session() as session:
        session.add(orm)
        await session.flush()
        return int(orm.id)


async def consume(
    db: Any, *, task_id: str, after_id: int = 0
) -> list[ControlSignalRecord]:
    """取出未消費的訊號並就地標記 `consumed_at`，保證同一筆不會被執行兩次。"""
    now = _utcnow()
    stmt = (
        select(ControlSignalModel)
        .where(
            ControlSignalModel.task_id == task_id,
            ControlSignalModel.consumed_at.is_(None),
            ControlSignalModel.id > after_id,
        )
        .order_by(ControlSignalModel.id)
    )
    async with db.session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        records: list[ControlSignalRecord] = []
        for orm in rows:
            orm.consumed_at = now
            records.append(
                ControlSignalRecord(
                    id=int(orm.id),
                    task_id=orm.task_id,
                    action=ControlAction(orm.action),
                    payload=dict(orm.payload) if orm.payload else None,
                    created_at=orm.created_at,
                    consumed_at=now,
                )
            )
        if records:
            await session.flush()
        return records


async def list_signals(
    db: Any, *, task_id: str, limit: int = 100
) -> list[ControlSignalRecord]:
    stmt = (
        select(ControlSignalModel)
        .where(ControlSignalModel.task_id == task_id)
        .order_by(ControlSignalModel.id)
        .limit(limit)
    )
    async with db.session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        return [
            ControlSignalRecord(
                id=int(orm.id),
                task_id=orm.task_id,
                action=ControlAction(orm.action),
                payload=dict(orm.payload) if orm.payload else None,
                created_at=orm.created_at,
                consumed_at=orm.consumed_at,
            )
            for orm in rows
        ]
