from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any

from adapters.payment import select_payment_provider
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from adapters.verification.ddddocr_provider import DdddOcrProvider
from adapters.verification.manual import ManualVerificationProvider
from adapters.verification.routing import RoutingVerificationProvider
from adapters.verification.rule_based import RuleBasedVerificationProvider
from api.schemas.ws import ServerMessageType
from broker.broker import SqliteTaskBroker
from broker.jobs import JobRecord
from broker.outbox import OutboxWriter
from browser.context_factory import BrowserProfile
from browser.system_chrome import SystemChromeError, ensure_system_chrome
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
    "CHALLENGE": "自動處理失敗，請在 Chrome 完成驗證（本程式不會代為繞過）",
    "VERIFICATION": "已自動填入辨識結果，請在瀏覽器確認後自行送出",
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
    experiment_id = uuid.uuid4().hex[:12]

    await broker.mark_running(job.id, worker_id=worker_id)

    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.update_status(task_id, TaskStatus.RUNNING)

    telemetry = StreamingTimelineRecorder(outbox, task_id, experiment_id=experiment_id)

    # 人機驗證只有使用者本機那顆真 Chrome 過得了，所以這裡自己把它開起來，
    # 而不是要使用者先手動開一個帶偵錯埠的瀏覽器再把端點貼進設定。
    cdp_endpoint = settings.cdp_endpoint
    if cdp_endpoint is None and settings.auto_launch_browser:
        try:
            chrome = await ensure_system_chrome(
                port=settings.browser_debug_port, initial_url=spec.event_url
            )
            cdp_endpoint = chrome.endpoint
        except SystemChromeError as exc:
            outbox.publish(
                task_id=task_id,
                experiment_id=experiment_id,
                type=ServerMessageType.TASK_LOG.value,
                payload={
                    "phase": "browser_launch_failed",
                    "reason": str(exc),
                },
                ephemeral=False,
            )
    borrowed = cdp_endpoint is not None
    browser = StreamingPlaywrightManager(
        BrowserProfile(name=job.profile, headless=settings.headless),
        telemetry,
        outbox=outbox,
        task_id=task_id,
        experiment_id=experiment_id,
        screenshot_dir=settings.screenshot_dir,
        cdp_endpoint=cdp_endpoint,
        cdp_page_url=spec.event_url if borrowed else None,
    )

    grace_s = settings.challenge_grace_s if spec.auto_cloudflare else 0.0
    ocr_on = settings.ocr_enabled and spec.auto_ocr

    if spec.verification_rules:
        text_provider: Any = RuleBasedVerificationProvider(
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

        text_provider = ManualVerificationProvider(
            prompt_for_answer, telemetry=telemetry
        )

    image_provider = (
        DdddOcrProvider(telemetry=telemetry, model_path=spec.ocr_model_path)
        if ocr_on
        else None
    )
    verification = RoutingVerificationProvider(text=text_provider, image=image_provider)

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

    async def announce_challenge_grace(
        kind: str, elapsed_s: float, budget_s: float, round_no: int
    ) -> None:
        outbox.publish(
            task_id=task_id,
            experiment_id=experiment_id,
            type=ServerMessageType.TASK_LOG.value,
            payload={
                "phase": "cloudflare_grace",
                "page_kind": kind,
                "elapsed_s": round(elapsed_s, 1),
                "budget_s": budget_s,
                "round": round_no,
                "max_rounds": spec.cloudflare_max_retries,
            },
            ephemeral=True,
        )

    async def announce_ocr_progress(attempt: int, max_retries: int) -> None:
        outbox.publish(
            task_id=task_id,
            experiment_id=experiment_id,
            type=ServerMessageType.TASK_LOG.value,
            payload={
                "phase": "ocr_processing",
                "attempt": attempt,
                "max_retries": max_retries,
            },
            ephemeral=True,
        )

    async def announce_verification_done(kind: str) -> None:
        outbox.publish(
            task_id=task_id,
            experiment_id=experiment_id,
            type=ServerMessageType.TASK_LOG.value,
            payload={
                "phase": "verification_completed",
                "kind": kind,
            },
            ephemeral=False,
        )

    adapter = KKTIXAdapter(
        telemetry=telemetry,
        payment=payment,
        verification=verification,
        attendees=spec.attendees,
        challenge_grace_s=grace_s,
        challenge_poll_s=settings.challenge_poll_s,
        cloudflare_max_retries=spec.cloudflare_max_retries,
        ocr_max_retries=spec.ocr_max_retries,
        debug_capture=spec.debug_screenshots_and_logs,
        on_challenge_grace=announce_challenge_grace,
        on_ocr_progress=announce_ocr_progress,
        on_verification_done=announce_verification_done,
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
                # 前端要能直接把人導到該處理的那一頁，而不是只叫他「去看瀏覽器」。
                "event_url": spec.event_url,
                # 沒有可見視窗時，這則通知是沒有人點得到的——前端要據此改口徑。
                "attended": borrowed or not settings.headless,
                "can_clear_bot_check": borrowed,
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
        session_gate_timeout_s=settings.session_gate_timeout_s,
        attended=borrowed or not settings.headless,
        # Playwright 自帶的瀏覽器過不了 Cloudflare，開視窗也一樣；只有借用模式可以。
        can_clear_bot_check=borrowed,
        challenge_grace_s=grace_s,
        challenge_poll_s=settings.challenge_poll_s,
        cloudflare_max_retries=spec.cloudflare_max_retries,
        auto_submit_verification=spec.auto_submit_verification,
        challenge_gate=announce_challenge_grace,
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
