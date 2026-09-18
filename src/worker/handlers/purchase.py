from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from adapters.payment import select_payment_provider
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from adapters.verification.manual import ManualVerificationProvider
from adapters.verification.rule_based import RuleBasedVerificationProvider
from api.schemas.ws import ServerMessageType
from broker.broker import SqliteTaskBroker
from broker.jobs import JobRecord
from broker.outbox import OutboxWriter
from browser.context_factory import BrowserProfile
from domain.task import TaskStatus
from purchase.orchestrator import PurchaseOrchestrator
from storage.database import Database
from storage.repositories.task_repository import TaskRepository

from ..browser import StreamingPlaywrightManager
from ..control import ControllableScheduler, ControlPoller, ControlState
from ..persistence import save_experiment
from ..settings import WorkerSettings
from ..spec_codec import rehydrate_spec
from ..telemetry_bridge import ClockTicker, StreamingTimelineRecorder

GATE_HINTS = {
    "CHALLENGE": "瀏覽器裡出現人機驗證，請自行通過（本程式不會代為繞過）",
    "LOGIN": "被導到登入頁，請在瀏覽器裡自行登入",
    "EVENT": "目前停在活動主頁，請自行點進購票登記頁",
    "ORDER": "目前停在訂單頁，請確認是不是拿錯網址",
    "UNKNOWN": "頁面無法辨識，請自行確認瀏覽器狀態",
}


async def execute_purchase(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    spec = rehydrate_spec(job.payload["spec"])
    task_id = spec.task_id
    experiment_id = f"exp_{uuid.uuid4().hex[:12]}"

    await broker.mark_running(job.id, worker_id=worker_id)

    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.update_status(task_id, TaskStatus.RUNNING)

    telemetry = StreamingTimelineRecorder(outbox, task_id, experiment_id=experiment_id)

    browser = StreamingPlaywrightManager(
        BrowserProfile(name=job.profile, headless=settings.headless),
        telemetry,
        outbox=outbox,
        task_id=task_id,
        experiment_id=experiment_id,
        screenshot_dir=settings.screenshot_dir,
    )

    if spec.verification_rules:
        verification: Any = RuleBasedVerificationProvider(
            spec.verification_rules, telemetry=telemetry
        )
    else:

        async def prompt_for_answer(challenge: Any) -> str:
            q_text = getattr(challenge, "question", str(challenge))
            outbox.publish(
                task_id=task_id,
                experiment_id=experiment_id,
                type=ServerMessageType.TASK_LOG.value,
                payload={
                    "phase": "verification_prompt",
                    "question": q_text,
                },
                ephemeral=False,
            )
            return ""

        verification = ManualVerificationProvider(
            prompt_for_answer, telemetry=telemetry
        )

    # adapter 由後端允許清單依執行模式決定；spec 內的欄位不是 adapter 名稱。
    payment = select_payment_provider(
        spec.execution_mode,
        telemetry=telemetry,
        payment_profile=spec.payment_profile,
    )
    outbox.publish(
        task_id=task_id,
        experiment_id=experiment_id,
        type=ServerMessageType.TASK_LOG.value,
        payload={
            "phase": "execution_mode",
            "execution_mode": spec.execution_mode.value,
            "payment_provider": payment.name,
        },
        ephemeral=False,
    )

    adapter = KKTIXAdapter(
        telemetry=telemetry,
        payment=payment,
        verification=verification,
        attendees=spec.attendees,
    )

    control_state = ControlState()
    scheduler = ControllableScheduler(telemetry, control=control_state)

    async def announce_gate(kind: str, attempt: int) -> None:
        outbox.publish(
            task_id=task_id,
            experiment_id=experiment_id,
            type=ServerMessageType.TASK_LOG.value,
            payload={
                "phase": "waiting_for_human",
                "page_kind": kind,
                "attempt": attempt,
                "hint": GATE_HINTS.get(kind, "請自行確認瀏覽器狀態"),
            },
            ephemeral=False,
        )

    settings.timeline_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = settings.timeline_dir / f"{experiment_id}_timeline.json"

    orchestrator = PurchaseOrchestrator(
        spec,
        browser=browser,
        scheduler=scheduler,
        adapter=adapter,
        telemetry=telemetry,
        timeline_path=timeline_path,
        session_gate=announce_gate,
    )

    ticker = ClockTicker(
        outbox,
        task_id,
        spec.sale_start_at,
        experiment_id=experiment_id,
        scheduler=scheduler,
        hz=settings.clock_tick_hz,
        timeout_seconds=spec.timeout_seconds,
    )
    ticker_task = asyncio.create_task(ticker.run())

    poller = ControlPoller(
        db,
        task_id,
        control_state,
        outbox=outbox,
        poll_interval_s=max(0.05, settings.control_poll_ms / 1000.0),
    )
    poller_task = asyncio.create_task(poller.run())

    report = None
    try:
        report = await orchestrator.run()
    finally:
        ticker.stop()
        poller.stop()
        ticker_task.cancel()
        poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker_task
        with contextlib.suppress(asyncio.CancelledError):
            await poller_task
        await scheduler.shutdown()
        await browser.stop()

    if report is not None:
        await save_experiment(
            db,
            experiment_id=experiment_id,
            task_id=task_id,
            report=report,
            events=telemetry.events(),
        )

        if report.final_state == "COMPLETED":
            task_status = TaskStatus.COMPLETED
            await broker.complete(
                job.id,
                worker_id=worker_id,
                result={
                    "experiment_id": experiment_id,
                    "final_state": report.final_state,
                },
            )
        elif report.aborted:
            task_status = TaskStatus.CANCELLED
            await broker.fail(
                job.id,
                worker_id=worker_id,
                error=report.error or "task_cancelled",
                retry=False,
            )
        else:
            task_status = TaskStatus.FAILED
            await broker.fail(
                job.id,
                worker_id=worker_id,
                error=report.error or f"orchestrator_{report.final_state.lower()}",
                retry=False,
            )

        async with db.session() as session:
            repo = TaskRepository(session)
            await repo.update_status(task_id, task_status, error_message=report.error)
