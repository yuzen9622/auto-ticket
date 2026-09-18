"""CLOCK_TICK 的 payload 組裝——Worker 與 API 共用同一份規則。

倒數分三個階段：開賣前數到開賣、開賣後數到本次搶票的逾時、任務結束後停止。
階段由**伺服器端**判定並寫進 payload，前端不得靠本地時間自行猜測 Worker 走到哪。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class ClockPhase(str, Enum):
    WAITING_FOR_SALE = "waiting_for_sale"
    TICKETING = "ticketing"
    FINISHED = "finished"


def _ms_between(target: datetime, now: datetime) -> float:
    return (target - now).total_seconds() * 1000.0


def ticketing_deadline(
    sale_start_at: datetime | None, timeout_seconds: int | float | None
) -> datetime | None:
    """本次搶票的逾時時刻；缺任一項就無法推導，回 None（前端顯示為未知而非 0）。"""
    if sale_start_at is None or timeout_seconds is None:
        return None
    try:
        seconds = float(timeout_seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return sale_start_at + timedelta(seconds=seconds)


def build_clock_payload(
    *,
    now: datetime,
    sale_start_at: datetime | None,
    deadline: datetime | None,
    clock_offset_ms: float = 0.0,
    finished: bool = False,
) -> dict[str, Any]:
    """組 CLOCK_TICK payload。

    兩個倒數都夾在 0 以上：已經過期的時間差是 0，不是負數。
    """
    server_time = now.isoformat()

    if finished:
        return {
            "server_time": server_time,
            "phase": ClockPhase.FINISHED.value,
            "time_to_sale_ms": None,
            "time_to_timeout_ms": None,
            "clock_offset_ms": clock_offset_ms,
        }

    adjusted = now + timedelta(milliseconds=clock_offset_ms)

    if sale_start_at is not None and adjusted < sale_start_at:
        return {
            "server_time": server_time,
            "phase": ClockPhase.WAITING_FOR_SALE.value,
            "time_to_sale_ms": max(0.0, _ms_between(sale_start_at, adjusted)),
            "time_to_timeout_ms": None,
            "clock_offset_ms": clock_offset_ms,
        }

    return {
        "server_time": server_time,
        "phase": ClockPhase.TICKETING.value,
        "time_to_sale_ms": 0.0,
        "time_to_timeout_ms": (
            None if deadline is None else max(0.0, _ms_between(deadline, adjusted))
        ),
        "clock_offset_ms": clock_offset_ms,
    }
