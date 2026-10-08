from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from scheduler.scheduler import WarmupStage
from tests.netguard import netguard_autouse  # noqa: F401
from worker.control import ControlState


async def test_control_state_pause_and_resume() -> None:
    control = ControlState()

    # 1. 預設未暫停，gate 直接通過
    await control.gate(WarmupStage.CHECK_SESSION)

    # 2. 暫停
    assert control.pause() is True

    gate_entered = False

    async def wait_gate():
        nonlocal gate_entered
        gate_entered = True
        await control.gate(WarmupStage.CHECK_SESSION)

    task = asyncio.create_task(wait_gate())
    await asyncio.sleep(0.01)
    assert gate_entered is True
    assert not task.done()

    # 3. 恢復
    control.resume()
    await task
    assert task.done()


async def test_control_state_emergency_stop_wakes_gate_deadlock_free() -> None:
    control = ControlState()
    assert control.pause() is True

    cancelled = False

    async def wait_gate():
        nonlocal cancelled
        try:
            await control.gate(WarmupStage.CHECK_SESSION)
        except asyncio.CancelledError:
            cancelled = True
            raise

    task = asyncio.create_task(wait_gate())
    await asyncio.sleep(0.01)
    assert not task.done()

    # 在暫停等待中發動 emergency_stop
    control.emergency_stop()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled is True


async def test_control_state_spin_started_refuses_pause() -> None:
    control = ControlState()

    # 進入 SPIN_WAIT 階段
    await control.gate(WarmupStage.SPIN_WAIT)

    # 此後拒絕暫停
    assert control.pause() is False


async def test_control_state_force_transition() -> None:
    control = ControlState()
    mock_fsm = MagicMock()
    mock_fsm.sync_to_state.return_value = True
    control.bind_fsm(mock_fsm)

    ok = control.force_transition("PAYMENT_REQUIRED")
    assert ok is True
    mock_fsm.sync_to_state.assert_called_once_with(
        "PAYMENT_REQUIRED", "force_transition"
    )


async def test_emergency_stop_cancels_running_stage():
    from telemetry.timeline import TimelineRecorder
    from worker.control import ControllableScheduler

    control = ControlState()
    scheduler = ControllableScheduler(TimelineRecorder(), control=control)
    entered = asyncio.Event()
    sent_order = []

    async def purchase(ctx):
        entered.set()
        await asyncio.sleep(0.05)
        sent_order.append(True)

    scheduler.register(WarmupStage.TRIGGER_PURCHASE, purchase)
    task = asyncio.create_task(scheduler._handlers[WarmupStage.TRIGGER_PURCHASE](None))
    await entered.wait()
    control.emergency_stop()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent_order == []


@pytest.mark.parametrize("sale_delay", [-1, 120])
async def test_emergency_stop_finishes_real_scheduler_inline_and_scheduled(sale_delay):
    from datetime import datetime, timedelta, UTC
    from telemetry.timeline import TimelineRecorder
    from worker.control import ControllableScheduler
    from tests.unit.test_purchase_orchestrator import make_spec
    from scheduler.clock_sync import ClockSynchronizer

    control = ControlState()
    scheduler = ControllableScheduler(
        TimelineRecorder(), control=control,
        clock_synchronizer=ClockSynchronizer(ntp_host=None),
    )
    entered = asyncio.Event()
    continued = []

    async def prepare(ctx):
        entered.set()
        await asyncio.sleep(1)
        continued.append(True)

    scheduler.register(WarmupStage.PREPARE_BROWSER, prepare)
    spec = make_spec().model_copy(update={"sale_start_at": datetime.now(UTC) + timedelta(seconds=sale_delay)})
    await scheduler.start()
    from fsm.machine import PurchaseWorkflow
    task = asyncio.create_task(scheduler.schedule(spec, fsm=PurchaseWorkflow(spec, scheduler._telemetry)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        control.emergency_stop()
        plan = await asyncio.wait_for(task, 1)
        assert plan.aborted
        assert plan.finished.is_set()
        assert continued == []
    finally:
        await scheduler.shutdown()


async def test_stop_between_stages_wakes_scheduler_without_waiting_for_next_stage():
    from datetime import datetime, timedelta, UTC
    from telemetry.timeline import TimelineRecorder
    from worker.control import ControllableScheduler
    from tests.unit.test_purchase_orchestrator import make_spec

    control = ControlState()
    scheduler = ControllableScheduler(TimelineRecorder(), control=control)
    spec = make_spec().model_copy(update={"sale_start_at": datetime.now(UTC) + timedelta(seconds=700)})
    await scheduler.start()
    try:
        plan = await scheduler.schedule(spec)
        assert not plan.finished.is_set()
        control.emergency_stop()
        assert await asyncio.wait_for(scheduler.wait_until_finished(spec.task_id), 0.1)
        assert plan.aborted
    finally:
        await scheduler.shutdown()
