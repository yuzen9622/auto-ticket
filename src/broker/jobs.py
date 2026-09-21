from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any


class JobKind(str, Enum):
        PURCHASE = "PURCHASE"
        SESSION_CHECK = "SESSION_CHECK"
        AUTO_LOGIN = "AUTO_LOGIN"
        MANUAL_LOGIN = "MANUAL_LOGIN"
        EVENT_HYDRATE = "EVENT_HYDRATE"


class JobState(str, Enum):
        PENDING = "PENDING"
        CLAIMED = "CLAIMED"
        RUNNING = "RUNNING"
        DONE = "DONE"
        FAILED = "FAILED"
        CANCELLED = "CANCELLED"


TERMINAL_JOB_STATES: frozenset[JobState] = frozenset(
        {JobState.DONE, JobState.FAILED, JobState.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class JobRecord:
        id: str
        kind: JobKind
        task_id: str | None
        profile: str
        payload: Any
        state: JobState
        available_at: datetime
        worker_id: str | None
        lease_expires_at: datetime | None
        heartbeat_at: datetime | None
        attempt: int
        max_attempts: int
        result: Any
        error: str | None
        created_at: datetime
        updated_at: datetime

        @classmethod
        def from_orm_row(cls, orm: Any) -> JobRecord:
                return cls(
                        id=orm.id,
                        kind=JobKind(orm.kind),
                        task_id=orm.task_id,
                        profile=orm.profile,
                        payload=MappingProxyType(dict(orm.payload or {})),
                        state=JobState(orm.state),
                        available_at=orm.available_at,
                        worker_id=orm.worker_id,
                        lease_expires_at=orm.lease_expires_at,
                        heartbeat_at=orm.heartbeat_at,
                        attempt=orm.attempt,
                        max_attempts=orm.max_attempts,
                        result=(
                                None
                                if orm.result is None
                                else MappingProxyType(dict(orm.result))
                        ),
                        error=orm.error,
                        created_at=orm.created_at,
                        updated_at=orm.updated_at,
                )
