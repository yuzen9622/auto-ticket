"""SQLite WAL 上的任務佇列：API Server 與 Worker 之間唯一的派工通道。

領取的原子性靠 compare-and-set（`UPDATE ... WHERE id=:id AND state='PENDING'`）。
SQLite 同時只有一個寫者，配合 `busy_timeout=5000`，多個 Worker 併行也不可能重複領取。

`[D4-7]`：任何 `async with db.session()` 區塊內都不得出現 I/O 或 sleep。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update

from broker.jobs import JobKind, JobRecord, JobState
from broker.models import BrokerJobModel, ControlSignalModel
from storage.models import PurchaseTaskModel

LEASE_EXPIRED_ERROR = "lease_expired"
LEASE_EXPIRED_TASK_MESSAGE = "Worker lease expired without heartbeat"
EMERGENCY_STOP_ACTION = "EMERGENCY_STOP"

_ACTIVE_STATES = (JobState.CLAIMED.value, JobState.RUNNING.value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id() -> str:
    return uuid.uuid4().hex[:16]


class SqliteTaskBroker:
    def __init__(
        self,
        db: Any,
        *,
        lease_ttl_s: float = 30.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db = db
        try:
            self._lease_ttl_s = float(lease_ttl_s)
        except (ValueError, TypeError):
            self._lease_ttl_s = 30.0
        self._clock = clock

    @property
    def lease_ttl_s(self) -> float:
        return self._lease_ttl_s

    async def enqueue(
        self,
        *,
        kind: JobKind,
        profile: str,
        payload: dict[str, Any],
        task_id: str | None = None,
        available_at: datetime | None = None,
        max_attempts: int = 1,
    ) -> str:
        now = self._clock()
        job_id = new_job_id()
        try:
            parsed_attempts = max(1, int(max_attempts))
        except (ValueError, TypeError):
            parsed_attempts = 1
        orm = BrokerJobModel(
            id=job_id,
            kind=JobKind(kind).value,
            task_id=task_id,
            profile=profile,
            payload=dict(payload),
            state=JobState.PENDING.value,
            available_at=available_at or now,
            attempt=0,
            max_attempts=parsed_attempts,
            created_at=now,
            updated_at=now,
        )
        async with self._db.session() as session:
            session.add(orm)
            await session.flush()
        return job_id

    async def claim(
        self,
        *,
        worker_id: str,
        kinds: Sequence[JobKind] | None = None,
        busy_profiles: Collection[str] = (),
    ) -> JobRecord | None:
        now = self._clock()
        expires = now + timedelta(seconds=self._lease_ttl_s)
        busy = tuple(dict.fromkeys(busy_profiles))

        stmt = select(BrokerJobModel.id).where(
            BrokerJobModel.state == JobState.PENDING.value,
            BrokerJobModel.available_at <= now,
        )
        if kinds:
            stmt = stmt.where(
                BrokerJobModel.kind.in_([JobKind(k).value for k in kinds])
            )
        # 空集合的 `NOT IN ()` 在部分方言是語法錯誤、在 SQLAlchemy 會退化成恆假警告；
        # 有 busy profile 才附加這個條件。
        if busy:
            stmt = stmt.where(BrokerJobModel.profile.not_in(busy))
        stmt = stmt.order_by(BrokerJobModel.available_at, BrokerJobModel.id).limit(1)

        async with self._db.session() as session:
            job_id = (await session.execute(stmt)).scalars().first()
            if job_id is None:
                return None
            claimed = await session.execute(
                update(BrokerJobModel)
                .where(
                    BrokerJobModel.id == job_id,
                    BrokerJobModel.state == JobState.PENDING.value,
                )
                .values(
                    state=JobState.CLAIMED.value,
                    worker_id=worker_id,
                    attempt=BrokerJobModel.attempt + 1,
                    lease_expires_at=expires,
                    heartbeat_at=now,
                    updated_at=now,
                )
            )
            if claimed.rowcount != 1:
                # 被別的 worker 搶走了。不重試——下一輪再來即可。
                return None
            orm = await session.get(BrokerJobModel, job_id)
            if orm is None:
                return None
            return JobRecord.from_orm_row(orm)

    async def heartbeat(self, job_id: str, *, worker_id: str) -> bool:
        now = self._clock()
        expires = now + timedelta(seconds=self._lease_ttl_s)
        async with self._db.session() as session:
            result = await session.execute(
                update(BrokerJobModel)
                .where(
                    BrokerJobModel.id == job_id,
                    BrokerJobModel.worker_id == worker_id,
                    BrokerJobModel.state.in_(_ACTIVE_STATES),
                )
                .values(heartbeat_at=now, lease_expires_at=expires, updated_at=now)
            )
            return result.rowcount == 1

    async def mark_running(self, job_id: str, *, worker_id: str) -> bool:
        now = self._clock()
        async with self._db.session() as session:
            result = await session.execute(
                update(BrokerJobModel)
                .where(
                    BrokerJobModel.id == job_id,
                    BrokerJobModel.worker_id == worker_id,
                    BrokerJobModel.state == JobState.CLAIMED.value,
                )
                .values(state=JobState.RUNNING.value, updated_at=now)
            )
            return result.rowcount == 1

    async def complete(
        self, job_id: str, *, worker_id: str, result: dict[str, Any]
    ) -> bool:
        now = self._clock()
        async with self._db.session() as session:
            outcome = await session.execute(
                update(BrokerJobModel)
                .where(
                    BrokerJobModel.id == job_id,
                    BrokerJobModel.worker_id == worker_id,
                    BrokerJobModel.state.in_(_ACTIVE_STATES),
                )
                .values(
                    state=JobState.DONE.value,
                    result=dict(result),
                    error=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            return outcome.rowcount == 1

    async def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retry: bool = False,
    ) -> bool:
        now = self._clock()
        async with self._db.session() as session:
            orm = await session.get(BrokerJobModel, job_id)
            if (
                orm is None
                or orm.worker_id != worker_id
                or orm.state not in _ACTIVE_STATES
            ):
                return False
            if retry and orm.attempt < orm.max_attempts:
                orm.state = JobState.PENDING.value
                orm.worker_id = None
                orm.lease_expires_at = None
                orm.heartbeat_at = None
            else:
                orm.state = JobState.FAILED.value
                orm.lease_expires_at = None
                await self._mark_task_failed(session, orm.task_id, error)
            orm.error = error
            orm.updated_at = now
            await session.flush()
            return True

    async def trigger_now(self, job_id_or_task_id: str) -> bool:
        """將 PENDING job 的 available_at 設為現在（立即由 worker 領取）。"""
        now = self._clock()
        async with self._db.session() as session:
            orm = await session.get(BrokerJobModel, job_id_or_task_id)
            if orm is None:
                stmt = (
                    select(BrokerJobModel)
                    .where(
                        BrokerJobModel.task_id == job_id_or_task_id,
                        BrokerJobModel.state == JobState.PENDING.value,
                    )
                    .order_by(BrokerJobModel.id)
                    .limit(1)
                )
                orm = (await session.execute(stmt)).scalars().first()
            if orm is None or orm.state != JobState.PENDING.value:
                return False
            orm.available_at = now
            orm.updated_at = now
            await session.flush()
            return True

    async def cancel(self, job_id_or_task_id: str) -> bool:
        """未領取者直接標記 CANCELLED；已領取／執行中只發 EMERGENCY_STOP。

        行程外硬殺會留下開著的瀏覽器與半寫的實驗資料，因此中止一律由 Worker 收斂。
        """
        now = self._clock()
        async with self._db.session() as session:
            orm = await session.get(BrokerJobModel, job_id_or_task_id)
            if orm is None:
                stmt = (
                    select(BrokerJobModel)
                    .where(
                        BrokerJobModel.task_id == job_id_or_task_id,
                        BrokerJobModel.state.not_in(
                            (
                                JobState.DONE.value,
                                JobState.FAILED.value,
                                JobState.CANCELLED.value,
                            )
                        ),
                    )
                    .order_by(BrokerJobModel.id)
                    .limit(1)
                )
                orm = (await session.execute(stmt)).scalars().first()
            if orm is None:
                return False
            if orm.state == JobState.PENDING.value:
                orm.state = JobState.CANCELLED.value
                orm.lease_expires_at = None
                orm.updated_at = now
                if orm.task_id:
                    task = await session.get(PurchaseTaskModel, orm.task_id)
                    if task is not None:
                        task.status = "CANCELLED"
                        task.finished_at = now
                await session.flush()
                return True
            if orm.state in _ACTIVE_STATES:
                session.add(
                    ControlSignalModel(
                        task_id=orm.task_id or orm.id,
                        action=EMERGENCY_STOP_ACTION,
                        payload={"reason": "cancel_requested"},
                        created_at=now,
                    )
                )
                await session.flush()
                return True
            return False

    async def reap_expired(self) -> int:
        """回收失聯 worker 的 lease。

        `attempt < max_attempts` 者回到 PENDING；否則標記 FAILED，並把對應的
        `PurchaseTaskModel` 一併打成 FAILED——否則 API 會把已孤立的崩潰任務
        永遠顯示為 RUNNING。
        """
        now = self._clock()
        stmt = select(BrokerJobModel).where(
            BrokerJobModel.state.in_(_ACTIVE_STATES),
            BrokerJobModel.lease_expires_at.is_not(None),
            BrokerJobModel.lease_expires_at < now,
        )
        reaped = 0
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            for orm in rows:
                if orm.attempt < orm.max_attempts:
                    orm.state = JobState.PENDING.value
                    orm.worker_id = None
                    orm.lease_expires_at = None
                    orm.heartbeat_at = None
                else:
                    orm.state = JobState.FAILED.value
                    orm.error = LEASE_EXPIRED_ERROR
                    orm.lease_expires_at = None
                    await self._mark_task_failed(
                        session, orm.task_id, LEASE_EXPIRED_TASK_MESSAGE
                    )
                orm.updated_at = now
                reaped += 1
            if reaped:
                await session.flush()
        return reaped

    async def get(self, job_id: str) -> JobRecord | None:
        async with self._db.session() as session:
            orm = await session.get(BrokerJobModel, job_id)
            return JobRecord.from_orm_row(orm) if orm is not None else None

    async def list_jobs(
        self,
        *,
        task_id: str | None = None,
        states: Sequence[JobState] | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        stmt = select(BrokerJobModel)
        if task_id is not None:
            stmt = stmt.where(BrokerJobModel.task_id == task_id)
        if states:
            stmt = stmt.where(
                BrokerJobModel.state.in_([JobState(s).value for s in states])
            )
        stmt = stmt.order_by(BrokerJobModel.created_at, BrokerJobModel.id).limit(limit)
        async with self._db.session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [JobRecord.from_orm_row(orm) for orm in rows]

    @staticmethod
    async def _mark_task_failed(
        session: Any, task_id: str | None, message: str
    ) -> None:
        if task_id is None:
            return
        task = await session.get(PurchaseTaskModel, task_id)
        if task is None:
            return
        task.status = "FAILED"
        task.error_message = message
        task.finished_at = _utcnow()
