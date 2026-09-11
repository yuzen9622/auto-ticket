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
