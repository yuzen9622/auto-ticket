from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class TimelineEventType(str, Enum):
    STAGE = "STAGE"
    TRANSITION = "TRANSITION"
    NETWORK = "NETWORK"
    NETWORK_RTT = "NETWORK_RTT"
    CLOCK_SYNC = "CLOCK_SYNC"
    MARK = "MARK"
    ERROR = "ERROR"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    sequence: int
    event_type: TimelineEventType
    name: str
    wall_time: datetime
    corrected_wall_time: datetime
    monotonic_us: int
    detail: Mapping[str, Any]


class TimelineRecorder:
    def __init__(
        self,
        *,
        clock_offset_ms: float = 0.0,
        perf_counter: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._offset_ms = float(clock_offset_ms)
        self._perf = perf_counter
        self._wall = wall_clock
        self._anchor_perf = self._perf()
        self._lock = threading.Lock()
        self._sequence = 0
        self._events: list[TimelineEvent] = []

    def record(
        self,
        event_type: TimelineEventType,
        name: str,
        **detail: Any,
    ) -> TimelineEvent:
        with self._lock:
            self._sequence += 1
            wall = self._wall()
            perf_now = self._perf()
            mono_us = max(0, round((perf_now - self._anchor_perf) * 1_000_000))
            corr_wall = wall + (
                datetime.fromtimestamp(self._offset_ms / 1000.0, timezone.utc)
                - datetime.fromtimestamp(0, timezone.utc)
            )
            event = TimelineEvent(
                sequence=self._sequence,
                event_type=event_type,
                name=name,
                wall_time=wall,
                corrected_wall_time=corr_wall,
                monotonic_us=mono_us,
                detail=MappingProxyType(dict(detail)),
            )
            self._events.append(event)
            return event

    def apply_offset(self, offset_ms: float) -> None:
        with self._lock:
            self._offset_ms = float(offset_ms)

    @property
    def clock_offset_ms(self) -> float:
        with self._lock:
            return self._offset_ms

    def record_stage(
        self,
        stage: str,
        planned_at: datetime,
        fired_at: datetime,
        drift_us: int,
        status: str = "OK",
        **extra: Any,
    ) -> TimelineEvent:
        return self.record(
            TimelineEventType.STAGE,
            stage,
            planned_at=planned_at.isoformat(),
            fired_at=fired_at.isoformat(),
            drift_us=drift_us,
            status=status,
            **extra,
        )

    def record_transition(
        self,
        from_state: str,
        to_state: str,
        event: str,
        trigger_us: int | None = None,
        **extra: Any,
    ) -> TimelineEvent:
        return self.record(
            TimelineEventType.TRANSITION,
            f"{from_state}->{to_state}",
            from_state=from_state,
            to_state=to_state,
            event=event,
            trigger_us=trigger_us if trigger_us is not None else 0,
            **extra,
        )

    def record_network_rtt(
        self,
        request_id: str,
        url: str,
        rtt_ms: float,
        **extra: Any,
    ) -> TimelineEvent:
        return self.record(
            TimelineEventType.NETWORK_RTT,
            "network_rtt",
            request_id=request_id,
            url=url,
            rtt_ms=rtt_ms,
            **extra,
        )

    def record_clock_sync(
        self,
        source: str,
        previous_offset_ms: float,
        offset_ms: float,
        delta_ms: float,
        applied: bool,
        **extra: Any,
    ) -> TimelineEvent:
        return self.record(
            TimelineEventType.CLOCK_SYNC,
            "clock_sync",
            source=source,
            previous_offset_ms=previous_offset_ms,
            offset_ms=offset_ms,
            delta_ms=delta_ms,
            applied=applied,
            **extra,
        )

    def record_error(self, name: str, exc: BaseException, **extra: Any) -> TimelineEvent:
        return self.record(
            TimelineEventType.ERROR,
            name,
            error_type=type(exc).__name__,
            error_message=str(exc),
            **extra,
        )

    def events(self) -> tuple[TimelineEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def events_of(self, event_type: TimelineEventType) -> tuple[TimelineEvent, ...]:
        with self._lock:
            return tuple(e for e in self._events if e.event_type == event_type)

    def export_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = [
                {
                    "sequence": e.sequence,
                    "event_type": e.event_type.value,
                    "name": e.name,
                    "wall_time": e.wall_time.isoformat(),
                    "corrected_wall_time": e.corrected_wall_time.isoformat(),
                    "monotonic_us": e.monotonic_us,
                    "detail": dict(e.detail),
                }
                for e in self._events
            ]
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
