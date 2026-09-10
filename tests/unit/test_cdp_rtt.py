"""CDP RTT 追蹤器：配對、記憶體上限與可重入鎖。"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from browser.cdp_rtt import DEFAULT_MAX_PENDING, DEFAULT_PENDING_TTL_S, CdpRttTracker
from telemetry.timeline import TimelineEventType, TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401


class StepPerf:
    """每次呼叫前進固定秒數的受控 perf 時鐘。"""

    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.value = start
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def req(request_id: str, url: str = "https://x.example.com/a") -> dict[str, Any]:
    return {"requestId": request_id, "request": {"url": url}}


def resp(request_id: str, status: int = 200) -> dict[str, Any]:
    return {"requestId": request_id, "response": {"status": status}}


@pytest.fixture
def telemetry() -> TimelineRecorder:
    return TimelineRecorder()


def test_matched_pair_records_rtt(telemetry: TimelineRecorder) -> None:
    perf = StepPerf(start=10.0)
    tracker = CdpRttTracker(telemetry, perf_counter=perf)
    tracker.on_request_will_be_sent(req("r1"))
    perf.value = 10.25
    tracker.on_response_received(resp("r1"))

    events = telemetry.events_of(TimelineEventType.NETWORK_RTT)
    assert len(events) == 1
    assert events[0].detail["request_id"] == "r1"
    assert events[0].detail["rtt_ms"] == pytest.approx(250.0)
    assert events[0].detail["status"] == 200
    assert events[0].detail["clock_anomaly"] is False
    assert tracker.stats()["matched"] == 1
    assert tracker.pending_count == 0


def test_orphan_response_is_counted(telemetry: TimelineRecorder) -> None:
    tracker = CdpRttTracker(telemetry)
    tracker.on_response_received(resp("never-sent"))
    assert tracker.stats()["orphan"] == 1
    assert telemetry.events_of(TimelineEventType.NETWORK_RTT) == ()


def test_missing_request_id_is_ignored(telemetry: TimelineRecorder) -> None:
    tracker = CdpRttTracker(telemetry)
    tracker.on_request_will_be_sent({"request": {"url": "u"}})
    tracker.on_response_received({"response": {"status": 200}})
    assert tracker.stats() == {"pending": 0, "matched": 0, "orphan": 0, "dropped": 0}


def test_clock_rollback_is_flagged_and_clamped(telemetry: TimelineRecorder) -> None:
    perf = StepPerf(start=50.0)
    tracker = CdpRttTracker(telemetry, perf_counter=perf)
    tracker.on_request_will_be_sent(req("r1"))
    perf.value = 49.0  # 時鐘回撥
    tracker.on_response_received(resp("r1"))
    detail = telemetry.events_of(TimelineEventType.NETWORK_RTT)[0].detail
    assert detail["clock_anomaly"] is True
    assert detail["rtt_ms"] == 0.0


def test_lru_eviction_at_max_pending(telemetry: TimelineRecorder) -> None:
    tracker = CdpRttTracker(telemetry, max_pending=3, perf_counter=StepPerf(step=0.0))
    for i in range(4):
        tracker.on_request_will_be_sent(req(f"r{i}"))
    assert tracker.pending_count == 3
    assert tracker.stats()["dropped"] == 1
    # 最舊的 r0 已被逐出 -> 其 response 變成 orphan
    tracker.on_response_received(resp("r0"))
    assert tracker.stats()["orphan"] == 1


def test_default_limits_match_contract() -> None:
    assert DEFAULT_MAX_PENDING == 500
    assert DEFAULT_PENDING_TTL_S == 60.0


def test_ttl_expiry_evicts_stale_entries(telemetry: TimelineRecorder) -> None:
    perf = StepPerf(start=0.0)
    tracker = CdpRttTracker(telemetry, pending_ttl_s=10.0, perf_counter=perf)
    tracker.on_request_will_be_sent(req("old"))
    perf.value = 100.0
    evicted = tracker.prune()
    assert evicted == 1
    assert tracker.pending_count == 0
    assert tracker.stats()["dropped"] == 1


def test_prune_stops_at_first_live_entry(telemetry: TimelineRecorder) -> None:
    perf = StepPerf(start=0.0)
    tracker = CdpRttTracker(telemetry, pending_ttl_s=10.0, perf_counter=perf)
    tracker.on_request_will_be_sent(req("old"))
    perf.value = 5.0  # 尚未超過 TTL，插入時不會順手清掉 old
    tracker.on_request_will_be_sent(req("fresh"))
    perf.value = 12.0  # old 已過期（12 > 10），fresh 未過期（12 - 5 = 7）
    assert tracker.prune() == 1
    assert tracker.pending_count == 1


def test_clear_empties_pending(telemetry: TimelineRecorder) -> None:
    tracker = CdpRttTracker(telemetry)
    tracker.on_request_will_be_sent(req("r1"))
    tracker.clear()
    assert tracker.pending_count == 0


def test_lock_is_reentrant(telemetry: TimelineRecorder) -> None:
    tracker = CdpRttTracker(telemetry)
    assert type(tracker._lock).__name__ == "RLock"


def test_on_request_will_be_sent_does_not_deadlock_on_ttl_path(telemetry: TimelineRecorder) -> None:
    """持鎖中觸發 TTL 清理，非重入鎖會在此永久卡住。"""
    perf = StepPerf(start=0.0)
    tracker = CdpRttTracker(telemetry, pending_ttl_s=1.0, perf_counter=perf)
    tracker.on_request_will_be_sent(req("stale"))
    perf.value = 100.0  # 下一次進入時必然走 TTL 清理路徑

    done = threading.Event()

    def worker() -> None:
        tracker.on_request_will_be_sent(req("fresh"))
        done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    assert thread.is_alive() is False
    assert done.is_set()
    assert tracker.pending_count == 1


def test_prune_and_prune_locked_agree_on_dropped_stats(telemetry: TimelineRecorder) -> None:
    perf = StepPerf(start=0.0)
    tracker = CdpRttTracker(telemetry, pending_ttl_s=5.0, perf_counter=perf)
    for i in range(3):
        tracker.on_request_will_be_sent(req(f"a{i}"))
    perf.value = 100.0
    with tracker._lock:
        evicted_locked = tracker._prune_locked(perf.value)
    assert evicted_locked == 3
    assert tracker.stats()["dropped"] == 3
    assert tracker.prune() == 0
    assert tracker.stats()["dropped"] == 3


def test_concurrent_mixed_calls_are_consistent(telemetry: TimelineRecorder) -> None:
    tracker = CdpRttTracker(telemetry, max_pending=1000)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            for i in range(50):
                key = f"w{worker_id}-{i}"
                tracker.on_request_will_be_sent(req(key))
                tracker.on_response_received(resp(key))
                tracker.prune()
                tracker.stats()
        except BaseException as exc:  # pragma: no cover - 失敗時才會用到
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(8)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert t.is_alive() is False
    elapsed = time.perf_counter() - started

    assert not errors
    assert elapsed < 5.0
    stats = tracker.stats()
    assert stats["matched"] == 400
    assert stats["orphan"] == 0
    assert stats["pending"] == 0
    assert len(telemetry.events_of(TimelineEventType.NETWORK_RTT)) == 400
