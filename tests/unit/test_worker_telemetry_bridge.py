from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from api.schemas.ws import ServerMessageType
from telemetry.timeline import TimelineEvent, TimelineEventType
from tests.netguard import netguard_autouse  # noqa: F401
from worker.telemetry_bridge import (
    ClockTicker,
    StreamingTimelineRecorder,
    map_timeline_event,
)


def test_map_timeline_event_transition() -> None:
    now = datetime.now(timezone.utc)
    ev = TimelineEvent(
        sequence=1,
        event_type=TimelineEventType.TRANSITION,
        name="enter_ticket_selection",
        wall_time=now,
        corrected_wall_time=now,
        monotonic_us=1500000,
        detail={"from_state": "SALE_OPEN", "to_state": "TICKET_SELECTION"},
    )
    msg = map_timeline_event(ev, task_id="task_123", experiment_id="exp_01")
    assert msg.type == ServerMessageType.STATE_CHANGED
    assert msg.task_id == "task_123"
    assert msg.experiment_id == "exp_01"
    assert msg.payload["from_state"] == "SALE_OPEN"
    assert msg.payload["to_state"] == "TICKET_SELECTION"
    assert msg.payload["elapsed_ms"] == 1500.0


def test_map_timeline_event_error() -> None:
    now = datetime.now(timezone.utc)
    ev = TimelineEvent(
        sequence=2,
        event_type=TimelineEventType.ERROR,
        name="captcha_failed",
        wall_time=now,
        corrected_wall_time=now,
        monotonic_us=2000000,
        detail={"error_type": "CaptchaError", "error_message": "failed to solve"},
    )
    msg = map_timeline_event(ev, task_id="task_123")
    assert msg.type == ServerMessageType.ERROR
    assert msg.payload["name"] == "captcha_failed"
    assert msg.payload["error_type"] == "CaptchaError"


def test_streaming_timeline_recorder_publishes_to_outbox() -> None:
    mock_outbox = MagicMock()
    recorder = StreamingTimelineRecorder(
        outbox=mock_outbox,
        task_id="task_abc",
        experiment_id="exp_xyz",
    )
    recorder.record(
        TimelineEventType.STAGE,
        "checking_session",
        status="ok",
    )
    mock_outbox.publish.assert_called_once()
    kwargs = mock_outbox.publish.call_args.kwargs
    assert kwargs["task_id"] == "task_abc"
    assert kwargs["type"] == ServerMessageType.TASK_LOG.value
    assert kwargs["payload"]["name"] == "checking_session"


async def test_clock_ticker_publishes_tick() -> None:
    mock_outbox = MagicMock()
    now = datetime.now(timezone.utc)
    sale_at = now + timedelta(seconds=10)
    mock_sched = MagicMock()
    mock_sched.time_reference.offset_ms = 5.0

    ticker = ClockTicker(
        outbox=mock_outbox,
        task_id="task_test",
        sale_start_at=sale_at,
        scheduler=mock_sched,
        hz=100.0,
    )
    task = asyncio.create_task(ticker.run())
    await asyncio.sleep(0.02)
    ticker.stop()
    await task

    mock_outbox.publish.assert_called()
    ticks = [c.kwargs for c in mock_outbox.publish.call_args_list]
    assert all(k["type"] == ServerMessageType.CLOCK_TICK.value for k in ticks)

    waiting = ticks[0]
    assert waiting["ephemeral"] is True
    assert waiting["payload"]["phase"] == "waiting_for_sale"
    assert waiting["payload"]["time_to_sale_ms"] > 0
    assert waiting["payload"]["time_to_timeout_ms"] is None
    assert waiting["payload"]["clock_offset_ms"] == 5.0

    # 收尾那一則必須留在 outbox，重連的前端才知道倒數已結束。
    final = ticks[-1]
    assert final["ephemeral"] is False
    assert final["payload"]["phase"] == "finished"
    assert final["payload"]["time_to_sale_ms"] is None
    assert final["payload"]["time_to_timeout_ms"] is None


async def test_clock_ticker_switches_to_ticketing_after_sale() -> None:
    mock_outbox = MagicMock()
    sale_at = datetime.now(timezone.utc) - timedelta(seconds=5)

    ticker = ClockTicker(
        outbox=mock_outbox,
        task_id="task_test",
        sale_start_at=sale_at,
        hz=100.0,
        timeout_seconds=120,
    )
    task = asyncio.create_task(ticker.run())
    await asyncio.sleep(0.02)
    ticker.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    payload = mock_outbox.publish.call_args_list[0].kwargs["payload"]
    assert payload["phase"] == "ticketing"
    assert payload["time_to_sale_ms"] == 0
    assert 0 < payload["time_to_timeout_ms"] <= 120_000


async def test_clock_ticker_never_reports_negative_countdowns() -> None:
    mock_outbox = MagicMock()
    sale_at = datetime.now(timezone.utc) - timedelta(seconds=600)

    ticker = ClockTicker(
        outbox=mock_outbox,
        task_id="task_test",
        sale_start_at=sale_at,
        hz=100.0,
        timeout_seconds=60,
    )
    task = asyncio.create_task(ticker.run())
    await asyncio.sleep(0.02)
    ticker.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    payload = mock_outbox.publish.call_args_list[0].kwargs["payload"]
    assert payload["time_to_sale_ms"] == 0
    assert payload["time_to_timeout_ms"] == 0
