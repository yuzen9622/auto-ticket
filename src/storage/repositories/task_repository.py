from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.task import PurchaseTaskRecord, PurchaseTaskSpec, TaskStatus
from storage.models import PurchaseTaskModel

_TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        spec: PurchaseTaskSpec,
        *,
        event_id: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> str:
        orm = PurchaseTaskModel(
            id=spec.task_id,
            event_id=event_id,
            status=TaskStatus.CREATED.value,
            spec=spec.to_persistable_dict(),
            scheduled_at=scheduled_at,
        )
        self._session.add(orm)
        await self._session.flush()
        return spec.task_id

    async def get(self, task_id: str) -> PurchaseTaskRecord | None:
        orm = await self._session.get(PurchaseTaskModel, task_id)
        return self._to_domain(orm) if orm is not None else None

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_message: str | None = None,
    ) -> bool:
        orm = await self._session.get(PurchaseTaskModel, task_id)
        if orm is None:
            return False
        orm.status = status.value
        if error_message is not None:
            orm.error_message = error_message
        now = datetime.now(timezone.utc)
        if status is TaskStatus.RUNNING and orm.started_at is None:
            orm.started_at = now
        if status in _TERMINAL_STATUSES:
            orm.finished_at = now
        await self._session.flush()
        return True

    async def list_by_status(self, status: TaskStatus) -> list[PurchaseTaskRecord]:
        stmt = (
            select(PurchaseTaskModel)
            .where(PurchaseTaskModel.status == status.value)
            .order_by(PurchaseTaskModel.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_domain(orm) for orm in rows]

    def _to_domain(self, orm: PurchaseTaskModel) -> PurchaseTaskRecord:
        return PurchaseTaskRecord(
            id=orm.id,
            event_id=orm.event_id,
            status=TaskStatus(orm.status),
            spec=orm.spec or {},
            scheduled_at=orm.scheduled_at,
            started_at=orm.started_at,
            finished_at=orm.finished_at,
            error_message=orm.error_message,
            created_at=orm.created_at,
        )
