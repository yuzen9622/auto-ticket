"""微秒級 Timeline 記錄器。"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telemetry.timeline import TimelineEvent, TimelineEventType, TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401

BASE_WALL = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """受控的 perf/wall 時鐘，讓時序斷言完全決定性。"""

    def __init__(self, start_perf: float = 100.0, start_wall: datetime = BASE_WALL) -> None:
        self.perf = start_perf
        self.wall = start_wall

    def advance(self, seconds: float) -> None:
        self.perf += seconds
        self.wall += timedelta(seconds=seconds)

    def perf_counter(self) -> float:
        return self.perf

    def wall_clock(self) -> datetime:
        return self.wall


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def recorder(clock: FakeClock) -> TimelineRecorder:
    return TimelineRecorder(perf_counter=clock.perf_counter, wall_clock=clock.wall_clock)


def test_sequence_increments_monotonically(recorder: TimelineRecorder) -> None:
    for _ in range(5):
        recorder.record(TimelineEventType.MARK, "m")
    assert [e.sequence for e in recorder.events()] == [1, 2, 3, 4, 5]


def test_monotonic_us_is_non_decreasing(recorder: TimelineRecorder, clock: FakeClock) -> None:
    recorder.record(TimelineEventType.MARK, "a")
    clock.advance(0.5)
    recorder.record(TimelineEventType.MARK, "b")
    clock.advance(1.25)
    recorder.record(TimelineEventType.MARK, "c")
    values = [e.monotonic_us for e in recorder.events()]
    assert values == sorted(values)
    assert values == [0, 500_000, 1_750_000]


def test_corrected_wall_time_applies_offset(clock: FakeClock) -> None:
    rec = TimelineRecorder(
        clock_offset_ms=250.0, perf_counter=clock.perf_counter, wall_clock=clock.wall_clock
    )
    event = rec.record(TimelineEventType.MARK, "m")
    assert event.corrected_wall_time - event.wall_time == timedelta(milliseconds=250)


def test_apply_offset_takes_effect_immediately(recorder: TimelineRecorder) -> None:
    before = recorder.record(TimelineEventType.MARK, "before")
    assert before.corrected_wall_time == before.wall_time
    recorder.apply_offset(-1000.0)
    after = recorder.record(TimelineEventType.MARK, "after")
    assert after.corrected_wall_time - after.wall_time == timedelta(seconds=-1)
    assert recorder.clock_offset_ms == -1000.0


def test_record_stage_captures_drift_and_status(recorder: TimelineRecorder) -> None:
    planned = BASE_WALL
    fired = BASE_WALL + timedelta(milliseconds=3)
    event = recorder.record_stage("SPIN_WAIT", planned, fired, 3000, status="OK", extra_flag=True)
    assert event.event_type is TimelineEventType.STAGE
    assert event.detail["planned_at"] == planned.isoformat()
    assert event.detail["fired_at"] == fired.isoformat()
    assert event.detail["drift_us"] == 3000
    assert event.detail["status"] == "OK"
    assert event.detail["extra_flag"] is True


def test_record_transition_shape(recorder: TimelineRecorder) -> None:
    event = recorder.record_transition("IDLE", "PREPARING", "prepare_session")
    assert event.name == "IDLE->PREPARING"
    assert event.detail["from_state"] == "IDLE"
    assert event.detail["to_state"] == "PREPARING"
    assert event.detail["event"] == "prepare_session"
    assert event.detail["trigger_us"] == 0


def test_record_network_rtt_and_clock_sync(recorder: TimelineRecorder) -> None:
    rtt = recorder.record_network_rtt("req-1", "https://x.example.com/a", 12.5, status=200)
    assert rtt.event_type is TimelineEventType.NETWORK_RTT
    assert rtt.detail["rtt_ms"] == 12.5
    sync = recorder.record_clock_sync(
        source="NTP", previous_offset_ms=0.0, offset_ms=5.0, delta_ms=5.0, applied=True
    )
    assert sync.event_type is TimelineEventType.CLOCK_SYNC
    assert sync.detail["applied"] is True


def test_record_error_captures_type_and_message(recorder: TimelineRecorder) -> None:
    event = recorder.record_error("boom", ValueError("bad thing"), task_id="t1")
    assert event.event_type is TimelineEventType.ERROR
    assert event.detail["error_type"] == "ValueError"
    assert event.detail["error_message"] == "bad thing"
    assert event.detail["task_id"] == "t1"


def test_events_of_filters_by_type(recorder: TimelineRecorder) -> None:
    recorder.record(TimelineEventType.MARK, "m")
    recorder.record_transition("A", "B", "e")
    recorder.record(TimelineEventType.MARK, "m2")
    assert len(recorder.events_of(TimelineEventType.MARK)) == 2
    assert len(recorder.events_of(TimelineEventType.TRANSITION)) == 1


def test_detail_mapping_is_read_only(recorder: TimelineRecorder) -> None:
    event = recorder.record(TimelineEventType.MARK, "m", a=1)
    with pytest.raises(TypeError):
        event.detail["a"] = 2  # type: ignore[index]


def test_timeline_event_is_frozen(recorder: TimelineRecorder) -> None:
    event = recorder.record(TimelineEventType.MARK, "m")
    assert isinstance(event, TimelineEvent)
    with pytest.raises(Exception):
        event.sequence = 99  # type: ignore[misc]


def test_export_json_round_trip(recorder: TimelineRecorder, tmp_path: Path) -> None:
    recorder.record(TimelineEventType.MARK, "m", note="中文")
    recorder.record_transition("IDLE", "PREPARING", "prepare_session")
    dest = tmp_path / "nested" / "timeline.json"
    recorder.export_json(dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert [row["sequence"] for row in payload] == [1, 2]
    assert payload[0]["detail"]["note"] == "中文"
    assert payload[1]["event_type"] == "TRANSITION"


def test_concurrent_writes_have_no_lost_updates() -> None:
    rec = TimelineRecorder()
    counter = itertools.count()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        for _ in range(50):
            rec.record(TimelineEventType.MARK, f"m{next(counter)}")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    events = rec.events()
    assert len(events) == 400
    assert sorted(e.sequence for e in events) == list(range(1, 401))
