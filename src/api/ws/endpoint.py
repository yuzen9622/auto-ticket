from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from broker import control
from broker.broker import SqliteTaskBroker
from broker.outbox import OutboxReader
from storage.database import Database
from telemetry.clock import build_clock_payload, ticketing_deadline

from .. import queries
from ..schemas.ws import (
    ClientAction,
    ClientCommand,
    ServerMessage,
    ServerMessageType,
)
from .hub import Subscription, WsHub

router = APIRouter(tags=["websocket"])


#: 任務走到這些狀態就不再倒數。
FINISHED_TASK_STATUS = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def build_clock_snapshot(task: Any, task_id: str) -> ServerMessage:
    """重連後先補一則 CLOCK_TICK，讓前端不必靠本地時間猜 Worker 走到哪一階段。

    Worker 的 ticker 只在任務執行期間跑；重新整理或重連時它可能早就停了，
    此時唯一知道階段的人是伺服器。
    """
    spec = dict(getattr(task, "spec", None) or {}) if task is not None else {}
    sale_start_at = _as_utc(getattr(task, "scheduled_at", None))
    status_value = str(getattr(task, "status", "")) if task is not None else ""

    return ServerMessage(
        type=ServerMessageType.CLOCK_TICK,
        task_id=task_id,
        timestamp=_utcnow_iso(),
        payload=build_clock_payload(
            now=datetime.now(timezone.utc),
            sale_start_at=sale_start_at,
            deadline=ticketing_deadline(sale_start_at, spec.get("timeout_seconds")),
            finished=task is None or status_value in FINISHED_TASK_STATUS,
        ),
    )


@router.websocket("/ws/tasks/{task_id}")
async def task_websocket_endpoint(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()

    app = websocket.app
    hub: WsHub = app.state.hub
    db: Database = app.state.db
    broker: SqliteTaskBroker = app.state.broker
    outbox_reader: OutboxReader = getattr(
        app.state, "outbox_reader", None
    ) or OutboxReader(db)

    sub: Subscription = await hub.subscribe(task_id)

    max_replayed_id = 0
    try:
        # 1. 抓取初始快照送出 TASK_LOG
        task = await queries.get_task(db, task_id)
        task_status = task.status if task else "UNKNOWN"
        jobs = await broker.list_jobs(task_id=task_id, limit=1)
        job_state = jobs[0].state.value if jobs else "UNKNOWN"

        experiments, _ = await queries.list_experiments(db, task_id=task_id, limit=1)
        exp_id = experiments[0].id if experiments else None

        snapshot_msg = ServerMessage(
            type=ServerMessageType.TASK_LOG,
            task_id=task_id,
            experiment_id=exp_id,
            timestamp=_utcnow_iso(),
            payload={
                "phase": "snapshot",
                "task_status": task_status,
                "job_state": job_state,
                "experiment_id": exp_id,
            },
        )
        await websocket.send_json(snapshot_msg.model_dump(mode="json"))
        await websocket.send_json(
            build_clock_snapshot(task, task_id).model_dump(mode="json")
        )

        # 2. 查詢歷史回放
        replayed = await outbox_reader.replay(task_id, limit=50)
        for r in replayed:
            msg = ServerMessage(
                type=ServerMessageType(r.type),
                task_id=r.task_id,
                experiment_id=r.experiment_id,
                timestamp=r.created_at.isoformat(),
                payload=dict(r.payload),
                outbox_id=r.id,
            )
            await websocket.send_json(msg.model_dump(mode="json"))
        if replayed:
            max_replayed_id = max(r.id for r in replayed)

        # 3. 雙向迴圈
        async def send_loop() -> None:
            while True:
                frame = await sub.queue.get()
                if frame.outbox_id is not None and frame.outbox_id <= max_replayed_id:
                    continue
                await websocket.send_json(frame.model_dump(mode="json"))

        async def recv_loop() -> None:
            while True:
                data = await websocket.receive_json()
                try:
                    cmd = ClientCommand.model_validate(data)
                except Exception as exc:
                    err = ServerMessage(
                        type=ServerMessageType.ERROR,
                        task_id=task_id,
                        timestamp=_utcnow_iso(),
                        payload={"reason": "invalid_command", "details": str(exc)},
                    )
                    await websocket.send_json(err.model_dump(mode="json"))
                    continue

                if cmd.task_id is not None and cmd.task_id != task_id:
                    err = ServerMessage(
                        type=ServerMessageType.ERROR,
                        task_id=task_id,
                        timestamp=_utcnow_iso(),
                        payload={"reason": "task_id_mismatch"},
                    )
                    await websocket.send_json(err.model_dump(mode="json"))
                    continue

                if cmd.action == ClientAction.FORCE_TRANSITION and not cmd.target_state:
                    err = ServerMessage(
                        type=ServerMessageType.ERROR,
                        task_id=task_id,
                        timestamp=_utcnow_iso(),
                        payload={"reason": "missing_target_state"},
                    )
                    await websocket.send_json(err.model_dump(mode="json"))
                    continue

                cmd_payload: dict[str, str] = {}
                if cmd.reason:
                    cmd_payload["reason"] = cmd.reason
                if cmd.target_state:
                    cmd_payload["target_state"] = cmd.target_state

                sig_id = await control.publish(
                    db,
                    task_id=task_id,
                    action=control.ControlAction(cmd.action.value),
                    payload=cmd_payload or None,
                )

                ack = ServerMessage(
                    type=ServerMessageType.TASK_LOG,
                    task_id=task_id,
                    timestamp=_utcnow_iso(),
                    payload={
                        "action": cmd.action.value,
                        "accepted": True,
                        "signal_id": sig_id,
                    },
                )
                await websocket.send_json(ack.model_dump(mode="json"))

        send_task = asyncio.create_task(send_loop())
        recv_task = asyncio.create_task(recv_loop())

        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                await t

    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(sub)
