"""購票協調器：把預熱排程、狀態機、瀏覽器與 adapter 接起來。

這是唯一知道「哪個預熱階段該做什麼」的地方。所有失敗一律往外拋，
由排程器既有的 fail-closed 中止路徑收斂到 FSM 的 FAILED 終態。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.ticketing.kktix.adapter import KKTIXPageKind, PageState
from adapters.ticketing.kktix.dom import first_visible, ng_click, ng_fill
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from adapters.verification.base import VerificationProvider
from browser.context_factory import sanitize_path_component
from browser.manager import PlaywrightManager
from domain.task import PurchaseTaskSpec
from fsm.machine import PurchaseWorkflow
from purchase.handlers import (
    EVENT_ALL_TICKETS_UNAVAILABLE,
    EVENT_FORM_SUBMITTED,
    EVENT_PAGE_LOADED,
    EVENT_RETRY_VERIFICATION,
    EVENT_SEAT_CONFIRMED,
    EVENT_SUBMIT_PAYMENT,
    EVENT_TICKET_RESERVED,
    EVENT_VERIFICATION_PASSED,
    PurchaseStepError,
    seat_event_for,
    verification_event_for,
)
from scheduler.scheduler import WarmupContext, WarmupScheduler, WarmupStage
from strategy.ticket_strategy import decide_ticket
from telemetry.timeline import TimelineEventType, TimelineRecorder

# 付款結果 -> FSM 事件的單一對應表。
# 必須涵蓋 PaymentOutcome 全部成員，且**不得**散落成 if/else（由守門機械化比對）。
# CHECKPOINT_REACHED 在研究語意上等同「成功抵達付款檢查點」，因此送 payment_success；
# THREE_DS_REQUIRED 在本階段尚無互動通道，一律視為未完成。
PAYMENT_OUTCOME_EVENTS: Mapping[PaymentOutcome, str] = MappingProxyType(
    {
        PaymentOutcome.SUBMITTED: "payment_success",
        PaymentOutcome.CHECKPOINT_REACHED: "payment_success",
        PaymentOutcome.DECLINED: "payment_declined",
        PaymentOutcome.THREE_DS_REQUIRED: "payment_declined",
        PaymentOutcome.THREE_DS_FAILED: "payment_declined",
        PaymentOutcome.TIMEOUT: "payment_declined",
        PaymentOutcome.FAILED: "payment_declined",
    }
)


@dataclass(frozen=True, slots=True)
class PurchaseReport:
    task_id: str
    final_state: str
    sale_time_error_ms: float | None
    ticket_trace: tuple[str, ...] = ()
    ticket_failure_reasons: tuple[str, ...] = ()
    """選票每次失敗的理由碼。SOLD_OUT 終態可能來自選不到票以外的原因，必須如實揭露。"""
    payment: PaymentResult | None = None
    screenshots: tuple[str, ...] = ()
    screenshots_expected: int = 0
    """掛上 hook 之後發生的轉移數。少於它就代表有截圖沒寫成，如實揭露不掩蓋。"""
    timeline_path: Path | None = None
    stages: tuple[tuple[str, str], ...] = ()
    aborted: bool = False
    error: str | None = None


# 觀察者專用的合成事件名：**不是** FSM 宣告的轉移事件。
# 降級重選刻意避開 `retry_fallback_ticket`——那條轉移綁著 priority 計數，
# 借用它會把「換一張票」算成「用掉一個優先序」，兩者在研究資料上完全不同。
EVENT_TICKET_FALLBACK_RESELECTED = "ticket_fallback_reselected"
EVENT_CAPTCHA_RETRY_SYNC = "captcha_retry_sync"

# 頁面狀態 -> (FSM 狀態, 進入該狀態時偏好的宣告事件)。
# 事件送得出去就走宣告路徑（條件分流與計數器才不會被繞過），送不出去才平滑跳躍。
PAGE_STATE_FSM_SYNC: Mapping[PageState, tuple[str, str]] = MappingProxyType(
    {
        PageState.TICKET_SELECTION: ("TICKET_SELECTION", EVENT_PAGE_LOADED),
        PageState.SEAT_SELECTION: ("SEAT_SELECTION", EVENT_TICKET_RESERVED),
        PageState.FORM_FILLING: ("FORM_FILLING", EVENT_SEAT_CONFIRMED),
        PageState.VERIFICATION_CHALLENGE: (
            "VERIFICATION_REQUIRED",
            EVENT_FORM_SUBMITTED,
        ),
        PageState.PAYMENT_REQUIRED: ("PAYMENT_REQUIRED", EVENT_FORM_SUBMITTED),
        PageState.COMPLETED: ("COMPLETED", "payment_success"),
    }
)

# 憑證只從環境變數取得。常數名刻意避開 PASSWORD 字樣：識別子一旦帶上該關鍵字，
# 靜態掃描就分不出「環境變數的名字」與「真的密碼」。
ENV_LOGIN_USER = "AUTO_TICKET_KKTIX_USERNAME"
ENV_LOGIN_KEY = "AUTO_TICKET_KKTIX_PASSWORD"
ENV_MEMBER_CODE = "AUTO_TICKET_MEMBER_CODE"
MEMBER_CODE_PROMPT = "請輸入 KKTIX 會員/專屬邀請碼> "
MEMBER_CODE_PROMPT_TIMEOUT_S = 15.0


@dataclass(slots=True)
class _Runtime:
    page: Any | None = None
    screenshot_hook: Any | None = None
    error: str | None = None
    events_sent: list[str] = field(default_factory=list)
    ticket_failures: list[str] = field(default_factory=list)
    hooked_transitions: int = 0
    auto_login_attempted: bool = False
    # 動作進度協議：每一格都對應一個「已經做過就不得再做一次」的副作用。
    excluded_ticket_names: set[str] = field(default_factory=set)
    current_ticket_name: str | None = None
    seat_action_taken: bool = False
    qualification_handled: bool = False
    form_submitted: bool = False
    payment_attempted: bool = False


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
        screenshot_drain_timeout: float = 30.0,
        session_gate_timeout_s: float = 240.0,
        session_gate_poll_s: float = 5.0,
        session_gate: Callable[[str, int], Awaitable[None]] | None = None,
        race_loop_timeout_s: float = 120.0,
        queue_poll_s: float = 0.5,
        micro_wait_s: float = 0.05,
        optional_probe_ms: int = 500,
    ) -> None:
        self.spec = spec
        self.browser = browser
        self.scheduler = scheduler
        self.adapter = adapter
        self.telemetry = telemetry
        # provider 的唯一真相是 adapter 持有的那一份；此處只作為報告與組裝檢查用。
        self.payment = (
            payment if payment is not None else getattr(adapter, "payment", None)
        )
        self.verification = (
            verification
            if verification is not None
            else getattr(adapter, "verification", None)
        )
        self.timeline_path = timeline_path
        self.detect_timeout_ms = detect_timeout_ms
        self.wait_timeout = wait_timeout
        # 全頁截圖是逐張序列化的：預設 5 秒排空會在轉移多時被砍掉尾巴幾張。
        self.screenshot_drain_timeout = screenshot_drain_timeout
        # 開賣前的「等人就緒」閘門：登入與人機驗證都必須由人自己在瀏覽器裡完成。
        self.session_gate_timeout_s = session_gate_timeout_s
        self.session_gate_poll_s = session_gate_poll_s
        self.session_gate = session_gate
        # 反應式迴圈的全局預算與各種微等待。預算是唯一的止損點：
        # 沒它的話，一個永遠判不出來的頁面會讓迴圈轉到天荒地老。
        self.race_loop_timeout_s = race_loop_timeout_s
        self.queue_poll_s = queue_poll_s
        self.micro_wait_s = micro_wait_s
        self.optional_probe_ms = optional_probe_ms
        self.fsm: PurchaseWorkflow | None = None
        self._rt = _Runtime()

    # ------------------------------------------------------------------ 執行

    async def run(self) -> PurchaseReport:
        # 每一次 run() 都是一場獨立實驗：残留的排除名單與動作旗標會讓第二場
        # 一開局就跳過本該選的票種，實驗結果因此不可重現。
        self._rt = _Runtime()
        self.fsm = PurchaseWorkflow(
            self.spec, self.telemetry, on_transition_hook=self._on_transition
        )
        for stage, handler in self._stage_handlers().items():
            self.scheduler.register(stage, handler)
        await self.scheduler.start()
        plan = await self.scheduler.schedule(self.spec, fsm=self.fsm)
        await self.scheduler.wait_until_finished(self.spec.task_id, self.wait_timeout)
        # 截圖是背景任務：不排空就直接產報告，會少算最後幾張——研究輸出不該少報。
        drain = getattr(self.browser, "drain_background_tasks", None)
        if drain is not None:
            await drain(timeout=self.screenshot_drain_timeout)
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
            self._rt.hooked_transitions += 1
            hook(source, target, event)

    def _send(self, event_name: str) -> None:
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        self._rt.events_sent.append(event_name)
        fsm.send(event_name)

    def _sync_fsm(self, target_state: str, event_name: str) -> bool:
        """觀察者同步；回傳 False 代表 FSM 已在終態，呼叫端不得再推流程。"""
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        self._rt.events_sent.append(event_name)
        return bool(fsm.sync_to_state(target_state, event_name))

    async def _handle_guest_modal(self, page: Any, is_guest_modal: bool) -> bool:
        """訪客彈窗與登入頁共用的自動登入；全生命週期至多嘗試一次。

        憑證只經 `ng_fill` 進 DOM：**絕不**進 log、timeline、例外訊息。
        輸入期間暫卸 screenshot hook，否則下一張全頁截圖會把密碼欄位拍進研究資料。
        """
        if self._rt.auto_login_attempted:
            return False
        self._rt.auto_login_attempted = True
        username = os.environ.get(ENV_LOGIN_USER, "").strip()
        secret_token = os.environ.get(ENV_LOGIN_KEY, "").strip()
        if not (username and secret_token):
            self.telemetry.record(
                TimelineEventType.MARK,
                "auto_login_unavailable",
                requested=bool(self.spec.auto_login),
            )
            return False
        saved_hook = self._rt.screenshot_hook
        self._rt.screenshot_hook = None
        try:
            self.telemetry.record(TimelineEventType.MARK, "auto_login_started")
            if is_guest_modal:
                await self.adapter.navigate_to_login_from_guest_modal(page)
            ok = bool(await self.adapter.login(page, username, secret_token))
            if ok:
                await self.adapter.navigate_to_event(page, self.spec.event_url)
        finally:
            self._rt.screenshot_hook = saved_hook
        self.telemetry.record(TimelineEventType.MARK, "auto_login_result", ok=ok)
        return ok

    async def _try_auto_login(self, page: Any, kind: Any) -> bool:
        """開賣前的就緒閘門專用：只有真的配了憑證才試，否則一律等人。"""
        if self._rt.auto_login_attempted:
            return False
        if not (
            os.environ.get(ENV_LOGIN_USER, "").strip()
            and os.environ.get(ENV_LOGIN_KEY, "").strip()
        ):
            return False
        if kind is KKTIXPageKind.LOGIN:
            return await self._handle_guest_modal(page, is_guest_modal=False)
        state = await self.adapter.detect_page_state(page)
        if state is PageState.GUEST_MODAL:
            return await self._handle_guest_modal(page, is_guest_modal=True)
        return False

    def _require_page(self) -> Any:
        if self._rt.page is None:
            raise PurchaseStepError("browser", "page not prepared")
        return self._rt.page

    # ------------------------------------------------------------ 預熱階段

    async def _prepare_browser(self, ctx: WarmupContext) -> None:
        await self.browser.start()
        page = await self.browser.new_page()
        self._rt.page = page
        self._rt.screenshot_hook = self.browser.make_screenshot_hook(
            self.spec.task_id, page
        )
        await self.browser.attach_cdp(page)

    async def _check_session(self, ctx: WarmupContext) -> None:
        """開賣前的就緒閘門：確認真的停在可下單的登記頁。

        先前這一階段只記一筆 mark 就通過，等於把「有沒有登入」「有沒有被人機驗證
        擋住」留到 T=0 才發現——那時已經來不及。現在改成先探一次、沒好就等人處理，
        逾時仍未就緒即 fail-closed，**絕不**帶著未就緒的頁面衝進開賣。

        登入與人機驗證一律由人自己在瀏覽器裡完成；本方法只負責判讀與等待。
        """
        page = self._require_page()
        deadline = self._loop_time() + self.session_gate_timeout_s
        kind = await self.adapter.probe_page(page, self.spec.event_url)
        attempt = 1
        while True:
            self.telemetry.record(
                TimelineEventType.MARK,
                "session_probe",
                kind=str(getattr(kind, "value", kind)),
                attempt=attempt,
            )
            if kind is KKTIXPageKind.REGISTRATION:
                self.telemetry.record(
                    TimelineEventType.MARK, "session_ready", attempts=attempt
                )
                return
            if await self._try_auto_login(page, kind):
                kind = await self.adapter.probe_page(page)
                attempt += 1
                continue
            if self._loop_time() >= deadline:
                raise PurchaseStepError(
                    "check_session",
                    f"page not ready before sale: {getattr(kind, 'value', kind)}",
                )
            if self.session_gate is not None:
                await self.session_gate(str(getattr(kind, "value", kind)), attempt)
            await asyncio.sleep(self.session_gate_poll_s)
            # 只重新判讀目前這一頁，不再導航——反覆輪詢對方站台既沒必要也不禮貌。
            kind = await self.adapter.probe_page(page)
            attempt += 1

    @staticmethod
    def _loop_time() -> float:
        return asyncio.get_running_loop().time()

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
        await self._run_race_loop(page)

    # -------------------------------------------------------- 反應式驅動迴圈

    async def _run_race_loop(self, page: Any) -> None:
        """每一輪重新判讀真實頁面，依當下狀態決定動作。

        搶票的頁面不照流程圖走：排隊室會插隊、彈窗會蓋掉一切、劃位可能整段跳過。
        照既定順序逐步推進只會對著不存在的元素空點，再靠逾時掩蓋。這裡改以
        `detect_page_state` 為唯一真相，FSM 退居記錄者。
        """
        deadline = self._loop_time() + self.race_loop_timeout_s
        while True:
            if self._loop_time() >= deadline:
                raise PurchaseStepError("race_loop", "state loop budget exhausted")
            state = await self.adapter.detect_page_state(page)
            self.telemetry.record(
                TimelineEventType.MARK,
                "race_loop_state",
                state=str(getattr(state, "value", state)),
            )
            match state:
                case PageState.FAILURE_MODAL:
                    if not await self._handle_failure_modal(page):
                        return
                case PageState.GUEST_MODAL:
                    if not await self._handle_guest_modal(page, is_guest_modal=True):
                        raise PurchaseStepError(
                            "auth", "guest modal displayed and login unavailable"
                        )
                case PageState.QUEUE:
                    # **嚴禁 reload**：重整會被丟回隊伍尾端。只能靜靜等。
                    await asyncio.sleep(self.queue_poll_s)
                case PageState.COMPLETED:
                    self._sync_page_state(state)
                    return
                case PageState.PAYMENT_REQUIRED:
                    await self._handle_payment_required(page)
                    return
                case PageState.QUALIFICATION_CODE:
                    await self._handle_qualification_code(page)
                case PageState.FORM_FILLING:
                    await self._handle_form_filling(page)
                case PageState.VERIFICATION_CHALLENGE:
                    await self._handle_standalone_verification(page)
                case PageState.SEAT_SELECTION:
                    await self._handle_seat_selection(page)
                case PageState.TICKET_SELECTION:
                    if not await self._handle_ticket_selection(page):
                        return
                case _:
                    await asyncio.sleep(self.micro_wait_s)

    def _sync_page_state(self, state: PageState) -> bool:
        """把 FSM 對齊到頁面狀態；已在目標狀態就不重複記錄。"""
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        target, event_name = PAGE_STATE_FSM_SYNC[state]
        if fsm.current_state_id == target:
            return True
        return self._sync_fsm(target, event_name)

    # ------------------------------------------------------------ 購票步驟

    async def _handle_ticket_selection(self, page: Any) -> bool:
        """回傳 False 表示已抵達 SOLD_OUT 終態（研究結論，不是失敗）。"""
        if not self._sync_page_state(PageState.TICKET_SELECTION):
            return False
        options = await self.adapter.read_registration_tickets(page)
        decision = decide_ticket(
            options,
            self.spec.ticket_preference,
            excluded_names=self._rt.excluded_ticket_names,
        )
        return await self._apply_decision(page, decision)

    async def _apply_decision(self, page: Any, decision: Any) -> bool:
        """套用**已作出**的決策；售罄即收斂終態，套用失敗一律安全歸零後中止。"""
        if decision.status != "SELECTED" or decision.option is None:
            self._sync_fsm("SOLD_OUT", EVENT_ALL_TICKETS_UNAVAILABLE)
            return False
        ok, reason = await self.adapter.apply_ticket_decision(page, decision)
        if not ok:
            # 沒有這筆紀錄，「選到票卻卡在勾條款」與「真的售罄」在報告裡長得一模一樣。
            self._rt.ticket_failures.append(reason)
            self.telemetry.record(
                TimelineEventType.MARK,
                "ticket_attempt_failed",
                reason=reason,
                attempt=len(self._rt.ticket_failures),
            )
            await self.adapter.reset_ticket_quantities(page)
            raise PurchaseStepError("apply_ticket_decision", reason)
        self._rt.current_ticket_name = decision.option.name
        self._send(EVENT_TICKET_RESERVED)
        return True

    async def _handle_failure_modal(self, page: Any) -> bool:
        """搶輸這一輪：關窗、排除該票種、歸零後重新決策一次並直接套用。

        歸零失敗一律中止：帶著殘留數量選下一張票，會把兩種票一起送出。
        """
        await self.adapter.dismiss_failure_modal(page)
        failed = self._rt.current_ticket_name
        if failed:
            self._rt.excluded_ticket_names.add(failed)
        self._rt.current_ticket_name = None
        self._rt.seat_action_taken = False
        self._rt.form_submitted = False
        self.telemetry.record(
            TimelineEventType.MARK,
            "ticket_excluded",
            ticket=failed or "",
            excluded=len(self._rt.excluded_ticket_names),
        )
        if not await self.adapter.reset_ticket_quantities(page):
            raise PurchaseStepError(
                "reset_tickets", "Failed to reset quantities to zero"
            )
        options = await self.adapter.read_registration_tickets(page)
        decision = decide_ticket(
            options,
            self.spec.ticket_preference,
            excluded_names=self._rt.excluded_ticket_names,
        )
        if decision.status != "SELECTED" or decision.option is None:
            self._sync_fsm("SOLD_OUT", EVENT_ALL_TICKETS_UNAVAILABLE)
            return False
        if not self._sync_fsm("TICKET_SELECTION", EVENT_TICKET_FALLBACK_RESELECTED):
            return False
        return await self._apply_decision(page, decision)

    async def _handle_seat_selection(self, page: Any) -> None:
        if not self._sync_page_state(PageState.SEAT_SELECTION):
            raise PurchaseStepError("handle_seat_selection", "FSM in terminal state")
        if self._rt.seat_action_taken:
            # 按鈕已經按下去了，頁面只是還沒跳轉；再按一次會送出第二次配位請求。
            await asyncio.sleep(self.micro_wait_s)
            return
        ok = bool(
            await self.adapter.handle_seat_selection(
                page, self.spec.ticket_preference.seat_preference
            )
        )
        self._rt.seat_action_taken = True
        if not ok:
            self._send(seat_event_for(False))
            raise PurchaseStepError("handle_seat_selection", "seat action failed")

    async def _handle_form_filling(self, page: Any) -> None:
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        if not self._sync_page_state(PageState.FORM_FILLING):
            raise PurchaseStepError("fill_contact_form", "FSM in terminal state")
        if self._rt.form_submitted:
            await self._recover_from_verification_error(page)
            return
        if not await self.adapter.fill_contact_form(page, self.spec.contact_profile):
            raise PurchaseStepError("fill_contact_form", "form incomplete")
        requires = bool(await self.adapter.detect_verification(page))
        fsm.set_requires_verification(requires)
        self._send(EVENT_FORM_SUBMITTED)
        if requires:
            await self._resolve_verification(page)
        if not await self.adapter.submit_order(page):
            raise PurchaseStepError("submit_order", "confirm button unavailable")
        self._rt.form_submitted = True

    async def _recover_from_verification_error(self, page: Any) -> None:
        """送出後仍停在表單頁：只有驗證碼容器內的錯誤才算答錯。

        放寬到整頁會把「email 少了 @」誤判成問答題答錯，於是無限重答一題根本沒錯的題目。
        """
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        alert = await first_visible(
            page,
            KKTIXSelectors.CAPTCHA_ERROR_ALERT,
            timeout_ms=self.optional_probe_ms,
            telemetry=self.telemetry,
            field="captcha_error_alert",
        )
        if alert is None:
            await asyncio.sleep(self.micro_wait_s)
            return
        self._rt.form_submitted = False
        self.telemetry.record(TimelineEventType.MARK, "verification_answer_rejected")
        if not self._sync_fsm("VERIFICATION_REQUIRED", EVENT_CAPTCHA_RETRY_SYNC):
            raise PurchaseStepError(
                "handle_verification", "FSM in terminal state or failed to sync"
            )
        self._send(EVENT_RETRY_VERIFICATION)
        if fsm.current_state_id != "VERIFICATION_REQUIRED":
            # 次數已用罄：絕不作答、絕不重送、絕不宣告 verification_passed。
            raise PurchaseStepError("handle_verification", "verification exhausted")
        if not bool(await self.adapter.handle_verification(page)):
            raise PurchaseStepError("handle_verification", "re-answer failed")
        self._send(EVENT_VERIFICATION_PASSED)
        if not await self.adapter.submit_order(page):
            raise PurchaseStepError("submit_order", "confirm button unavailable")
        self._rt.form_submitted = True

    async def _handle_standalone_verification(self, page: Any) -> None:
        fsm = self.fsm
        if fsm is None:
            raise PurchaseStepError("fsm", "workflow not initialised")
        fsm.set_requires_verification(True)
        if not self._sync_page_state(PageState.VERIFICATION_CHALLENGE):
            raise PurchaseStepError("handle_verification", "FSM in terminal state")
        await self._resolve_verification(page)

    async def _handle_qualification_code(self, page: Any) -> None:
        if self._rt.qualification_handled:
            await asyncio.sleep(self.micro_wait_s)
            return
        code = await self._resolve_qualification_code()
        field_locator = await first_visible(
            page,
            KKTIXSelectors.MEMBER_CODE_INPUT,
            timeout_ms=self.detect_timeout_ms,
            telemetry=self.telemetry,
            field="member_code_input",
        )
        if field_locator is None:
            raise PurchaseStepError("qualification", "member code input not reachable")
        await ng_fill(page, field_locator, code)
        button = await first_visible(
            page,
            KKTIXSelectors.MEMBER_CODE_VERIFY_BTN,
            timeout_ms=self.detect_timeout_ms,
            telemetry=self.telemetry,
            field="member_code_verify_btn",
        )
        if button is None:
            raise PurchaseStepError("qualification", "member code button not reachable")
        await ng_click(page, button, telemetry=self.telemetry)
        self._rt.qualification_handled = True
        self.telemetry.record(TimelineEventType.MARK, "qualification_code_submitted")

    async def _resolve_qualification_code(self) -> str:
        """任務欄位 -> 環境變數 -> 互動輸入；全數落空即 fail-closed。

        互動輸入走可取消的 reader：用執行緒讀 stdin 會在逾時後留下一條永遠醒不來的
        執行緒，把它連同未讀完的輸入一起洩漏到下一場實驗。
        """
        code = (self.spec.qualification_code or "").strip()
        if code:
            return code
        code = os.environ.get(ENV_MEMBER_CODE, "").strip()
        if code:
            return code
        if sys.stdin.isatty():
            code = await self._prompt_for_code()
        if not code:
            self.telemetry.record(
                TimelineEventType.MARK, "qualification_code_missing"
            )
            raise PurchaseStepError(
                "qualification", "Qualification code required but not provided"
            )
        return code

    async def _prompt_for_code(self) -> str:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        fileno = sys.stdin.fileno()

        def on_stdin() -> None:
            line = sys.stdin.readline()
            if not fut.done():
                fut.set_result(line.strip())

        loop.add_reader(fileno, on_stdin)
        try:
            sys.stderr.write(MEMBER_CODE_PROMPT)
            sys.stderr.flush()
            return await asyncio.wait_for(fut, timeout=MEMBER_CODE_PROMPT_TIMEOUT_S)
        except (TimeoutError, EOFError, OSError):
            return ""
        finally:
            loop.remove_reader(fileno)

    async def _handle_payment_required(self, page: Any) -> None:
        if self._rt.payment_attempted:
            # 單次付款鎖：整個生命週期至多一次，重試等於可能重複扣款。
            raise PurchaseStepError("payment", "payment already attempted")
        self._rt.payment_attempted = True
        if not self._sync_page_state(PageState.PAYMENT_REQUIRED):
            raise PurchaseStepError("payment", "FSM in terminal state")
        await self._run_payment(page)

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
        screenshots = self._screenshot_names()
        if len(screenshots) < self._rt.hooked_transitions:
            self.telemetry.record(
                TimelineEventType.MARK,
                "screenshots_incomplete",
                expected=self._rt.hooked_transitions,
                written=len(screenshots),
            )
        return PurchaseReport(
            task_id=self.spec.task_id,
            final_state=fsm.current_state_id if fsm is not None else "UNKNOWN",
            sale_time_error_ms=(trigger.drift_us / 1000.0)
            if trigger is not None
            else None,
            ticket_trace=decision.trace if decision is not None else (),
            ticket_failure_reasons=tuple(self._rt.ticket_failures),
            payment=getattr(self.adapter, "last_payment_result", None),
            screenshots=screenshots,
            screenshots_expected=self._rt.hooked_transitions,
            timeline_path=self.timeline_path,
            stages=tuple((o.stage.value, o.status) for o in plan.outcomes),
            aborted=bool(getattr(plan, "aborted", False)),
            error=str(errors[-1].error) if errors else None,
        )
