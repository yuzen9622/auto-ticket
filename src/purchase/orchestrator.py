"""購票協調器：把預熱排程、狀態機、瀏覽器與 adapter 接起來。

這是唯一知道「哪個預熱階段該做什麼」的地方。所有失敗一律往外拋，
由排程器既有的 fail-closed 中止路徑收斂到 FSM 的 FAILED 終態。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.verification.base import VerificationProvider
from browser.context_factory import sanitize_path_component
from browser.manager import PlaywrightManager
from domain.task import PurchaseTaskSpec
from fsm.machine import PurchaseWorkflow
from purchase.handlers import (
    EVENT_ALL_TICKETS_UNAVAILABLE,
    EVENT_FORM_SUBMITTED,
    EVENT_PAGE_LOADED,
    EVENT_RETRY_FALLBACK_TICKET,
    EVENT_SUBMIT_PAYMENT,
    EVENT_TICKET_RESERVED,
    EVENT_VERIFICATION_PASSED,
    FATAL_TICKET_REASONS,
    PurchaseStepError,
    seat_event_for,
    ticket_event_for,
    verification_event_for,
)
from scheduler.scheduler import WarmupContext, WarmupScheduler, WarmupStage
from telemetry.timeline import TimelineEventType, TimelineRecorder

# 付款結果 -> FSM 事件的單一對應表。
# 必須涵蓋 PaymentOutcome 全部成員，且**不得**散落成 if/else（由守門機械化比對）。
# CHECKPOINT_REACHED 在研究語意上等同「成功抵達付款檢查點」，因此送 payment_success；
# THREE_DS_REQUIRED 在本階段尚無互動通道，一律視為未完成。
PAYMENT_OUTCOME_EVENTS: Mapping[PaymentOutcome, str] = MappingProxyType({
    PaymentOutcome.SUBMITTED: "payment_success",
    PaymentOutcome.CHECKPOINT_REACHED: "payment_success",
    PaymentOutcome.DECLINED: "payment_declined",
    PaymentOutcome.THREE_DS_REQUIRED: "payment_declined",
    PaymentOutcome.THREE_DS_FAILED: "payment_declined",
    PaymentOutcome.TIMEOUT: "payment_declined",
    PaymentOutcome.FAILED: "payment_declined",
})


@dataclass(frozen=True, slots=True)
class PurchaseReport:
    task_id: str
    final_state: str
    sale_time_error_ms: float | None
    ticket_trace: tuple[str, ...] = ()
    payment: PaymentResult | None = None
    screenshots: tuple[str, ...] = ()
    timeline_path: Path | None = None
    stages: tuple[tuple[str, str], ...] = ()
    aborted: bool = False
    error: str | None = None


@dataclass(slots=True)
class _Runtime:
    page: Any | None = None
    screenshot_hook: Any | None = None
    error: str | None = None
    events_sent: list[str] = field(default_factory=list)


class PurchaseOrchestrator:
    def __init__(
        self,
        spec: PurchaseTaskSpec,
        *,
        browser: PlaywrightManager,
        scheduler: WarmupScheduler,
        adapter: Any,
        telemetry: TimelineRecorder,
        payment: PaymentProvider | None = None,
        verification: VerificationProvider | None = None,
        timeline_path: Path | None = None,
        detect_timeout_ms: int = 5000,
        wait_timeout: float | None = None,
    ) -> None:
        self.spec = spec
        self.browser = browser
        self.scheduler = scheduler
        self.adapter = adapter
        self.telemetry = telemetry
        # provider 的唯一真相是 adapter 持有的那一份；此處只作為報告與組裝檢查用。
        self.payment = payment if payment is not None else getattr(adapter, "payment", None)
        self.verification = (
            verification if verification is not None else getattr(adapter, "verification", None)
        )
        self.timeline_path = timeline_path
        self.detect_timeout_ms = detect_timeout_ms
        self.wait_timeout = wait_timeout
        self.fsm: PurchaseWorkflow | None = None
        self._rt = _Runtime()

    # ------------------------------------------------------------------ 執行

    async def run(self) -> PurchaseReport:
        self.fsm = PurchaseWorkflow(
            self.spec, self.telemetry, on_transition_hook=self._on_transition
        )
        for stage, handler in self._stage_handlers().items():
            self.scheduler.register(stage, handler)
        await self.scheduler.start()
        plan = await self.scheduler.schedule(self.spec, fsm=self.fsm)
        await self.scheduler.wait_until_finished(self.spec.task_id, self.wait_timeout)
        if self.timeline_path is not None:
            self.telemetry.export_json(self.timeline_path)
        return self._build_report(plan)

    def _stage_handlers(self) -> dict[WarmupStage, Any]:
        return {
            WarmupStage.PREPARE_BROWSER: self._prepare_browser,
            WarmupStage.CHECK_SESSION: self._check_session,
            WarmupStage.NAVIGATE_PAGE: self._navigate_page,
            WarmupStage.ENTER_READY: self._enter_ready,
            WarmupStage.SPIN_WAIT: self._spin_wait,
            WarmupStage.TRIGGER_PURCHASE: self._trigger_purchase,
        }

    def _on_transition(self, source: str, target: str, event: str) -> None:
        hook = self._rt.screenshot_hook
        if hook is not None:
            hook(source, target, event)

    def _send(self, event_name: str) -> None:
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        self._rt.events_sent.append(event_name)
        fsm.send(event_name)

    def _require_page(self) -> Any:
        if self._rt.page is None:
            raise PurchaseStepError("browser", "page not prepared")
        return self._rt.page

    # ------------------------------------------------------------ 預熱階段

    async def _prepare_browser(self, ctx: WarmupContext) -> None:
        await self.browser.start()
        page = await self.browser.new_page()
        self._rt.page = page
        self._rt.screenshot_hook = self.browser.make_screenshot_hook(self.spec.task_id, page)
        await self.browser.attach_cdp(page)

    async def _check_session(self, ctx: WarmupContext) -> None:
        self.telemetry.record(
            TimelineEventType.MARK,
            "session_checked",
            task_id=self.spec.task_id,
            page_ready=self._rt.page is not None,
        )

    async def _navigate_page(self, ctx: WarmupContext) -> None:
        page = self._require_page()
        if not await self.adapter.navigate_to_event(page, self.spec.event_url):
            raise PurchaseStepError("navigate_to_event", "navigation refused")

    async def _enter_ready(self, ctx: WarmupContext) -> None:
        page = self._require_page()
        opened = await self.adapter.detect_sale_opened(page, self.detect_timeout_ms)
        self.telemetry.record(
            TimelineEventType.MARK, "pre_sale_probe", opened=bool(opened)
        )

    async def _spin_wait(self, ctx: WarmupContext) -> None:
        self.telemetry.record(
            TimelineEventType.MARK, "spin_wait_entered", drift_us=ctx.drift_us
        )

    async def _trigger_purchase(self, ctx: WarmupContext) -> None:
        page = self._require_page()
        self._send(EVENT_PAGE_LOADED)
        if not await self._run_ticket_selection(page):
            return
        await self._run_seat_selection(page)
        await self._run_form_and_verification(page)
        await self._run_payment(page)

    # ------------------------------------------------------------ 購票步驟

    async def _run_ticket_selection(self, page: Any) -> bool:
        """回傳 False 表示已抵達 SOLD_OUT 終態（研究結論，不是失敗）。"""
        fsm = self.fsm
        assert fsm is not None
        while True:
            ok, reason = await self.adapter.select_tickets(page, self.spec.ticket_preference)
            if reason in FATAL_TICKET_REASONS:
                raise PurchaseStepError("select_tickets", reason)
            event = ticket_event_for(reason)
            if ok and event == EVENT_TICKET_RESERVED:
                self._send(EVENT_TICKET_RESERVED)
                return True
            if event == EVENT_ALL_TICKETS_UNAVAILABLE:
                self._send(EVENT_ALL_TICKETS_UNAVAILABLE)
                return False
            self._send(EVENT_RETRY_FALLBACK_TICKET)
            if fsm.current_state_id != "TICKET_SELECTION":
                # 降級已把 FSM 帶到 SOLD_OUT：優先序用罄，如實停在該終態。
                return False

    async def _run_seat_selection(self, page: Any) -> None:
        ok = bool(
            await self.adapter.handle_seat_selection(
                page, self.spec.ticket_preference.seat_preference
            )
        )
        self._send(seat_event_for(ok))
        if not ok:
            raise PurchaseStepError("handle_seat_selection", "seat action failed")

    async def _run_form_and_verification(self, page: Any) -> None:
        fsm = self.fsm
        assert fsm is not None
        if not await self.adapter.fill_contact_form(page, self.spec.contact_profile):
            raise PurchaseStepError("fill_contact_form", "form incomplete")

        requires = bool(await self.adapter.detect_verification(page))
        fsm.set_requires_verification(requires)
        self._send(EVENT_FORM_SUBMITTED)
        if requires:
            await self._resolve_verification(page)
        if not await self.adapter.submit_order(page):
            raise PurchaseStepError("submit_order", "confirm button unavailable")

    async def _resolve_verification(self, page: Any) -> None:
        fsm = self.fsm
        assert fsm is not None
        while True:
            solved = bool(await self.adapter.handle_verification(page))
            event = verification_event_for(solved, fsm.can_retry_verification())
            self._send(event)
            if event == EVENT_VERIFICATION_PASSED:
                return
            if fsm.current_state_id != "VERIFICATION_REQUIRED":
                raise PurchaseStepError("handle_verification", "verification exhausted")

    async def _run_payment(self, page: Any) -> None:
        self._send(EVENT_SUBMIT_PAYMENT)
        result = await self.adapter.execute_payment(page, self.spec.payment_profile)
        event = PAYMENT_OUTCOME_EVENTS[result.outcome]
        self._send(event)

    # ------------------------------------------------------------------ 報告

    def _screenshot_names(self) -> tuple[str, ...]:
        directory = getattr(self.browser, "screenshot_dir", None)
        if directory is None or not Path(directory).exists():
            return ()
        prefix = f"{sanitize_path_component(self.spec.task_id, 'default')}_"
        return tuple(sorted(p.name for p in Path(directory).glob(f"{prefix}*")))

    def _build_report(self, plan: Any) -> PurchaseReport:
        fsm = self.fsm
        trigger = plan.outcome_of(WarmupStage.TRIGGER_PURCHASE)
        decision = getattr(self.adapter, "last_ticket_decision", None)
        errors = [
            o for o in plan.outcomes if o.status == "FAILED" and o.error is not None
        ]
        return PurchaseReport(
            task_id=self.spec.task_id,
            final_state=fsm.current_state_id if fsm is not None else "UNKNOWN",
            sale_time_error_ms=(trigger.drift_us / 1000.0) if trigger is not None else None,
            ticket_trace=decision.trace if decision is not None else (),
            payment=getattr(self.adapter, "last_payment_result", None),
            screenshots=self._screenshot_names(),
            timeline_path=self.timeline_path,
            stages=tuple((o.stage.value, o.status) for o in plan.outcomes),
            aborted=bool(getattr(plan, "aborted", False)),
            error=str(errors[-1].error) if errors else None,
        )
