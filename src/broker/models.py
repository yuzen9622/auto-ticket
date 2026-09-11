from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BrokerBase(DeclarativeBase):
    """Broker 專屬 metadata。

    嚴禁改成 `storage.models.Base`：那是凍結模組的私有型別，借用會讓本批的
    三張表混進凍結模組的 metadata，`create_all` 的責任邊界也跟著糊掉。
    """


class UtcDateTime(TypeDecorator):
    """語意與 `storage.models.UtcDateTime` 一致：storage 凍結，故在此重新宣告一份。"""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("naive datetime rejected at broker storage boundary")
        return value.astimezone(timezone.utc)

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerJobModel(BrokerBase):
    __tablename__ = "broker_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 刻意不設 FK 到 purchase_tasks：跨 metadata 的外鍵會把凍結模組拉進本表的
    # create_all 相依圖，且兩邊建表順序無法保證。
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    available_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_broker_jobs_state_available", "state", "available_at"),
        Index("ix_broker_jobs_task_id", "task_id"),
    )


class ControlSignalModel(BrokerBase):
    __tablename__ = "control_signals"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    __table_args__ = (
        Index("ix_control_signals_task_consumed", "task_id", "consumed_at", "id"),
    )


class OutboxEventModel(BrokerBase):
    __tablename__ = "broker_outbox"

    # 單調遞增的主鍵同時是 API 端的尾隨游標；不得改成 UUID。
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    ephemeral: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_broker_outbox_task_id", "task_id", "id"),)
