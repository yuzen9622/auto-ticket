from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from api.schemas.ws import ServerMessage, ServerMessageType
from broker.outbox import OutboxWriter
from telemetry.timeline import (
    TimelineEvent,
    TimelineEventType,
    TimelineRecorder,
)


def map_timeline_event(
    event: TimelineEvent,
    task_id: str,
    experiment_id: str | None = None,
) -> ServerMessage:
    elapsed_ms = event.monotonic_us / 1000.0
    ts = event.wall_time.isoformat()

    if event.event_type == TimelineEventType.TRANSITION:
        from_state = event.detail.get("from_state") or event.detail.get("source") or ""
        to_state = event.detail.get("to_state") or event.detail.get("target") or ""
        ev_name = event.detail.get("event") or event.name
        return ServerMessage(
            type=ServerMessageType.STATE_CHANGED,
            task_id=task_id,
            experiment_id=experiment_id,
            timestamp=ts,
            payload={
                "from_state": str(from_state),
                "to_state": str(to_state),
                "event": str(ev_name),
                "elapsed_ms": elapsed_ms,
            },
        )

    if event.event_type == TimelineEventType.ERROR:
        return ServerMessage(
            type=ServerMessageType.ERROR,
            task_id=task_id,
            experiment_id=experiment_id,
            timestamp=ts,
            payload={
                "name": event.name,
                "error_type": str(event.detail.get("error_type", "Error")),
                "error_message": str(event.detail.get("error_message", "")),
            },
        )

    return ServerMessage(
        type=ServerMessageType.TASK_LOG,
        task_id=task_id,
        experiment_id=experiment_id,
        timestamp=ts,
        payload={
            "event_type": event.event_type.value,
            "name": event.name,
            "detail": dict(event.detail),
        },
    )


class StreamingTimelineRecorder(TimelineRecorder):
    """將記錄的 TimelineEvent 同步推送至 OutboxWriter。"""

    def __init__(
        self,
        outbox: OutboxWriter,
        task_id: str,
        experiment_id: str | None = None,
        *,
        clock_offset_ms: float = 0.0,
        perf_counter: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"clock_offset_ms": clock_offset_ms}
        if perf_counter is not None:
            kwargs["perf_counter"] = perf_counter
        if wall_clock is not None:
            kwargs["wall_clock"] = wall_clock
        super().__init__(**kwargs)
        self._outbox = outbox
        self._task_id = task_id
        self._experiment_id = experiment_id

    def record(
        self,
        event_type: TimelineEventType,
        name: str,
        **detail: Any,
    ) -> TimelineEvent:
        event = super().record(event_type, name, **detail)
        msg = map_timeline_event(
            event,
            task_id=self._task_id,
            experiment_id=self._experiment_id,
        )
        self._outbox.publish(
            task_id=self._task_id,
            experiment_id=self._experiment_id,
            type=msg.type.value,
            payload=msg.payload,
            ephemeral=False,
        )
        return event


class ClockTicker:
    """定期在開賣前產生 CLOCK_TICK 訊息發給 Outbox。"""

    def __init__(
        self,
        outbox: OutboxWriter,
        task_id: str,
        sale_start_at: datetime,
        *,
        experiment_id: str | None = None,
        scheduler: Any = None,
        hz: float = 1.0,
    ) -> None:
        self._outbox = outbox
        self._task_id = task_id
        self._sale_start_at = sale_start_at
        self._experiment_id = experiment_id
        self._scheduler = scheduler
        try:
            val_hz = float(hz)
        except Exception:
            val_hz = 1.0
        self._interval_s = max(0.05, 1.0 / max(0.1, val_hz))
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            now_utc = datetime.now(timezone.utc)
            offset_ms = 0.0
            if self._scheduler is not None and hasattr(
                self._scheduler, "time_reference"
            ):
                ref = self._scheduler.time_reference
                if ref is not None:
                    try:
                        offset_ms = float(getattr(ref, "offset_ms", 0.0))
                    except Exception:
                        offset_ms = 0.0

            adjusted_now = now_utc + timedelta(milliseconds=offset_ms)
            time_to_sale_ms = (
                self._sale_start_at - adjusted_now
            ).total_seconds() * 1000.0

            if time_to_sale_ms <= 0:
                # 已過開賣時間，停止 ticker
                break

            self._outbox.publish(
                task_id=self._task_id,
                experiment_id=self._experiment_id,
                type=ServerMessageType.CLOCK_TICK.value,
                payload={
                    "server_time": now_utc.isoformat(),
                    "time_to_sale_ms": time_to_sale_ms,
                    "clock_offset_ms": offset_ms,
                },
                ephemeral=True,
            )

            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._running = False
