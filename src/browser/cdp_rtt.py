from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telemetry.timeline import TimelineRecorder

DEFAULT_MAX_PENDING = 500
DEFAULT_PENDING_TTL_S = 60.0


@dataclass(slots=True)
class PendingRequest:
    request_id: str
    url: str
    timestamp_wall: float
    timestamp_perf: float


class CdpRttTracker:
    def __init__(
        self,
        telemetry: TimelineRecorder,
        *,
        max_pending: int = DEFAULT_MAX_PENDING,
        pending_ttl_s: float = DEFAULT_PENDING_TTL_S,
        perf_counter: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._telemetry = telemetry
        self._max_pending = max_pending
        self._ttl_s = pending_ttl_s
        self._perf = perf_counter
        # 必須是可重入鎖：持鎖的 public 方法會再呼叫
        # 同樣需要鎖語意的清理邏輯，非重入鎖會直接自鎖死結。
        self._lock = threading.RLock()
        self._pending: OrderedDict[str, PendingRequest] = OrderedDict()
        self._matched_count = 0
        self._orphan_count = 0
        self._dropped_count = 0

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def on_request_will_be_sent(self, params: dict[str, Any]) -> None:
        req_id = params.get("requestId")
        req_obj = params.get("request", {})
        url = req_obj.get("url", "")
        if not req_id:
            return
        now_perf = self._perf()
        now_wall = time.time()
        with self._lock:
            # 持鎖中一律呼叫持鎖版私有方法，不得呼叫 public prune()
            self._prune_locked(now_perf)
            if len(self._pending) >= self._max_pending:
                self._pending.popitem(last=False)
                self._dropped_count += 1
            self._pending[req_id] = PendingRequest(
                request_id=req_id,
                url=url,
                timestamp_wall=now_wall,
                timestamp_perf=now_perf,
            )

    def on_response_received(self, params: dict[str, Any]) -> None:
        req_id = params.get("requestId")
        resp_obj = params.get("response", {})
        status = resp_obj.get("status")
        if not req_id:
            return
        now_perf = self._perf()
        with self._lock:
            pending = self._pending.pop(req_id, None)
            if pending is None:
                self._orphan_count += 1
                return
            self._matched_count += 1

        rtt_sec = now_perf - pending.timestamp_perf
        clock_anomaly = rtt_sec < 0
        rtt_ms = 0.0 if clock_anomaly else round(rtt_sec * 1000.0, 3)

        self._telemetry.record_network_rtt(
            request_id=req_id,
            url=pending.url,
            rtt_ms=rtt_ms,
            status=status,
            clock_anomaly=clock_anomaly,
        )

    def prune(self, current_perf: float | None = None) -> int:
        """對外的 TTL 清理入口（自行取鎖）。"""
        now_perf = current_perf if current_perf is not None else self._perf()
        with self._lock:
            return self._prune_locked(now_perf)

    def _prune_locked(self, now_perf: float) -> int:
        """呼叫端必須已持有 self._lock。

        OrderedDict 依插入序（即 perf 時序）排列，故遇到第一個未過期項目即可提早 break。
        """
        expired_keys: list[str] = []
        for k, v in self._pending.items():
            if now_perf - v.timestamp_perf > self._ttl_s:
                expired_keys.append(k)
            else:
                break
        for k in expired_keys:
            self._pending.pop(k, None)
        evicted = len(expired_keys)
        self._dropped_count += evicted
        return evicted

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "pending": len(self._pending),
                "matched": self._matched_count,
                "orphan": self._orphan_count,
                "dropped": self._dropped_count,
            }
