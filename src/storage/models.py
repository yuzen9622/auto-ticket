from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UtcDateTime(TypeDecorator):
    """Timezone-aware UTC datetime column; rejects naive values at the storage boundary."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("naive datetime rejected at storage boundary")
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


class EventModel(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    organizer: Mapped[str] = mapped_column(String(128), nullable=False)
    event_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(512), nullable=False)
    sale_start_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    event_start_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow
    )

    ticket_types: Mapped[list[TicketTypeModel]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TicketTypeModel(Base):
    __tablename__ = "ticket_types"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("events.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    inventory_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    event: Mapped[EventModel | None] = relationship(back_populates="ticket_types")


class PurchaseTaskModel(Base):
    __tablename__ = "purchase_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("events.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class ExperimentModel(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("purchase_tasks.id", ondelete="CASCADE"), nullable=True
    )
    strategy_used: Mapped[str] = mapped_column(String(64), nullable=False)
    clock_sync_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    sale_time_error_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_state: Mapped[str] = mapped_column(String(64), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class ExperimentEventModel(Base):
    __tablename__ = "experiment_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    experiment_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    elapsed_ms: Mapped[float] = mapped_column(Float, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    state_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ExperimentMetricsModel(Base):
    __tablename__ = "experiment_metrics"

    experiment_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scheduler_error_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    sale_detection_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_page_load_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ticket_selection_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    seat_selection_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    form_fill_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    payment_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    selector_fallback_count: Mapped[int] = mapped_column(Integer, default=0)
