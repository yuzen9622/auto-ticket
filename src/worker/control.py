from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from api.schemas.ws import ServerMessageType
from broker import control
from broker.outbox import OutboxWriter
from scheduler.scheduler import WarmupContext, WarmupPlan, WarmupScheduler, WarmupStage
from storage.database import Database
from telemetry.timeline import TimelineRecorder

logger = logging.getLogger(__name__)


class ControlState:
    """管理購票任務的控制狀態：暫停、恢復、緊急中止與強制轉移。"""

    def __init__(self) -> None:
        self._paused = asyncio.Event()
        self._paused.set()  # 預設非暫停
        self._aborted = asyncio.Event()
        self._spin_started = False
        self._fsm: Any = None

    def bind_fsm(self, fsm: Any) -> None:
        self._fsm = fsm

    async def gate(self, stage: WarmupStage) -> None:
        """階段進入前檢查閘門；支援暫停與緊急中止喚醒。

        SPIN_WAIT 與 TRIGGER_PURCHASE 不設閘門，進入前標記 spin_started。
        """
        if self._aborted.is_set():
            raise asyncio.CancelledError("Task aborted by EMERGENCY_STOP")

        if self._spin_started or stage in (
            WarmupStage.SPIN_WAIT,
            WarmupStage.TRIGGER_PURCHASE,
        ):
            self._spin_started = True
            return

        while not self._paused.is_set():
            resume_task = asyncio.create_task(self._paused.wait())
            abort_task = asyncio.create_task(self._aborted.wait())
            done, pending = await asyncio.wait(
                [resume_task, abort_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if self._aborted.is_set():
                raise asyncio.CancelledError("Task aborted while paused")

    def pause(self) -> bool:
        if self._spin_started:
            return False
        self._paused.clear()
        return True

    def resume(self) -> None:
        self._paused.set()

    def emergency_stop(self) -> None:
        self._aborted.set()
        self._paused.set()  # 喚醒任何卡在 gate() 的協程

    def mark_spin_started(self) -> None:
        self._spin_started = True

    def force_transition(self, target_state: str) -> bool:
        if self._fsm is None:
            return False
        sync_fn = getattr(self._fsm, "sync_to_state", None)
        if callable(sync_fn):
            try:
                return bool(sync_fn(target_state, "force_transition"))
            except Exception:
                return False
        return False


class ControllableScheduler(WarmupScheduler):
    """WarmupScheduler 的可控子類別，在 stage 進入前插入 control.gate。"""

    def __init__(
        self,
        telemetry: TimelineRecorder,
        *,
        control: ControlState,
        **kwargs: Any,
    ) -> None:
        super().__init__(telemetry, **kwargs)
        self._control = control

    def register(
        self,
        stage: WarmupStage,
        handler: Callable[[WarmupContext], Awaitable[None]],
    ) -> None:
        ctrl = self._control

        async def wrapped_handler(ctx: WarmupContext) -> None:
            await ctrl.gate(stage)
            if stage in (WarmupStage.SPIN_WAIT, WarmupStage.TRIGGER_PURCHASE):
                ctrl.mark_spin_started()
            await handler(ctx)

        super().register(stage, wrapped_handler)

    def attach_fsm(self, task_id: str, fsm: Any) -> None:
        self._control.bind_fsm(fsm)
        super().attach_fsm(task_id, fsm)

    async def schedule(
        self,
        task_or_spec: Any,
        sale_start_at: Any = None,
        fsm: Any = None,
        server_url: Any = None,
    ) -> WarmupPlan:
        if fsm is not None:
            self._control.bind_fsm(fsm)
        return await super().schedule(
            task_or_spec,
            sale_start_at=sale_start_at,
            fsm=fsm,
            server_url=server_url,
        )


class ControlPoller:
    """非同步輪詢 broker/control.py 的 signals，轉調 ControlState。"""

    def __init__(
        self,
        db: Database,
        task_id: str,
        control_state: ControlState,
        outbox: OutboxWriter | None = None,
        *,
        poll_interval_s: float = 0.2,
    ) -> None:
        self._db = db
        self._task_id = task_id
        self._control = control_state
        self._outbox = outbox
        try:
            self._poll_interval_s = float(poll_interval_s)
        except Exception:
            self._poll_interval_s = 0.2
        self._running = False
        self._last_signal_id = 0

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                signals = await control.consume(
                    self._db,
                    task_id=self._task_id,
                    after_id=self._last_signal_id,
                )
                for sig in signals:
                    if sig.id > self._last_signal_id:
                        self._last_signal_id = sig.id
                    self._handle_signal(sig)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Control poller error: %s", exc)

            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                break

    def _handle_signal(self, sig: control.ControlSignalRecord) -> None:
        if sig.action == control.ControlAction.EMERGENCY_STOP:
            self._control.emergency_stop()
        elif sig.action == control.ControlAction.PAUSE:
            ok = self._control.pause()
            if not ok and self._outbox is not None:
                self._outbox.publish(
                    task_id=self._task_id,
                    type=ServerMessageType.ERROR.value,
                    payload={
                        "name": "pause_refused",
                        "error_type": "ControlError",
                        "error_message": "pause_refused_after_spin_wait",
                    },
                    ephemeral=False,
                )
        elif sig.action == control.ControlAction.RESUME:
            self._control.resume()
        elif sig.action == control.ControlAction.FORCE_TRANSITION:
            target_state = (sig.payload or {}).get("target_state", "")
            ok = self._control.force_transition(target_state)
            if not ok and self._outbox is not None:
                self._outbox.publish(
                    task_id=self._task_id,
                    type=ServerMessageType.ERROR.value,
                    payload={
                        "name": "force_transition_failed",
                        "error_type": "ControlError",
                        "error_message": f"force transition to '{target_state}' failed",
                    },
                    ephemeral=False,
                )

    def stop(self) -> None:
        self._running = False
