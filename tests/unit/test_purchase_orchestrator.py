from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adapters.payment.base import PaymentOutcome, PaymentResult
from adapters.ticketing.kktix.adapter import (
    ALL_TICKET_REASONS,
    REASON_NO_TICKET_UNITS,
    REASON_NOT_REGISTRATION_PAGE,
    REASON_PLUS_BUTTON_MISSING,
    REASON_QUANTITY_MISMATCH,
    REASON_SELECTED,
    REASON_SOLD_OUT,
    REASON_TERMS_NOT_ACCEPTED,
    KKTIXPageKind,
    PageState,
)
from domain.preference import SeatPreference, TicketPreference, TicketPriority
from domain.task import PurchaseTaskSpec, UserContactProfile
from purchase.handlers import (
    FATAL_TICKET_REASONS,
    TICKET_REASON_EVENTS,
    PurchaseStepError,
)
from purchase.orchestrator import PAYMENT_OUTCOME_EVENTS, PurchaseOrchestrator
from scheduler.clock_sync import TimeReference
from scheduler.scheduler import StageOutcome, WarmupContext, WarmupStage
from strategy.ticket_strategy import TicketDecision, TicketOption
from telemetry.timeline import TimelineRecorder
from tests.fake_page import FakeBrowser, FakePage
from tests.netguard import netguard_autouse  # noqa: F401

BASE_WALL = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
TRIGGER_DRIFT_US = 4200
ALL_STAGES = tuple(WarmupStage)


@dataclass
class StubPlan:
    outcomes: list[StageOutcome] = field(default_factory=list)
    aborted: bool = False

    def outcome_of(self, stage: WarmupStage) -> StageOutcome | None:
        for outcome in self.outcomes:
            if outcome.stage == stage:
                return outcome
        return None


class StubScheduler:
    """複刻真實排程器的階段順序、FSM 驅動點與 fail-closed 中止路徑。"""

    def __init__(self) -> None:
        self.handlers: dict[WarmupStage, Any] = {}
        self.started = False
        self.plan = StubPlan()
        self.fsm: Any = None

    def register(self, stage: WarmupStage, handler: Any) -> None:
        if stage in self.handlers:
            raise ValueError(f"Handler already registered for stage: {stage}")
        self.handlers[stage] = handler

    async def start(self) -> None:
        self.started = True

    async def schedule(
        self, spec: PurchaseTaskSpec, fsm: Any = None, **kwargs: Any
    ) -> StubPlan:
        self.fsm = fsm
        for stage in ALL_STAGES:
            drift = TRIGGER_DRIFT_US if stage is WarmupStage.TRIGGER_PURCHASE else 0
            if stage is WarmupStage.PREPARE_BROWSER and fsm.current_state_id == "IDLE":
                fsm.send("prepare_session")
            if stage is WarmupStage.TRIGGER_PURCHASE:
                fsm.send("sale_triggered")
            try:
                await self.handlers[stage](
                    WarmupContext(
                        task_id=spec.task_id,
                        stage=stage,
                        planned_local_at=BASE_WALL,
                        fired_local_at=BASE_WALL,
                        drift_us=drift,
                        time_reference=None,
                    )
                )
            except Exception as exc:
                self.plan.outcomes.append(
                    StageOutcome(stage, "FAILED", drift, error=exc)
                )
                self.plan.aborted = True
                if fsm.can_send("abort_failed"):
                    fsm.send("abort_failed")
                return self.plan
            self.plan.outcomes.append(StageOutcome(stage, "OK", drift))
            if stage is WarmupStage.CHECK_SESSION:
                fsm.send("session_ready")
        return self.plan

    async def wait_until_finished(
        self, task_id: str, timeout: float | None = None
    ) -> bool:
        return True


class StubAdapter:
    name = "stub"

    def __init__(
        self,
        *,
        page_states: list[PageState] | None = None,
        probe_kinds: list[KKTIXPageKind] | None = None,
        registration_tickets: list[TicketOption] | None = None,
        ticket_results: list[tuple[bool, str]] | None = None,
        seat_ok: bool = True,
        form_ok: bool = True,
        requires_verification: bool = False,
        verification_results: list[bool] | None = None,
        submit_ok: bool = True,
        payment_outcome: PaymentOutcome = PaymentOutcome.CHECKPOINT_REACHED,
        reset_ok: bool = True,
        dismiss_ok: bool = True,
        login_ok: bool = True,
    ) -> None:
        self.page_states = (
            list(page_states)
            if page_states is not None
            else [
                PageState.TICKET_SELECTION,
                PageState.SEAT_SELECTION,
                PageState.FORM_FILLING,
                PageState.PAYMENT_REQUIRED,
            ]
        )
        self.probe_kinds = list(probe_kinds or [])
        self.registration_tickets = registration_tickets
        self.ticket_results = list(ticket_results or [(True, REASON_SELECTED)])
        self.seat_ok = seat_ok
        self.form_ok = form_ok
        self.requires_verification = requires_verification
        self.verification_results = list(verification_results or [True])
        self.submit_ok = submit_ok
        self.payment_outcome = payment_outcome
        self.reset_ok = reset_ok
        self.dismiss_ok = dismiss_ok
        self.login_ok = login_ok
        self.on_login: Any = None
        self.calls: list[str] = []
        self.payment = "stub-payment-provider"
        self.verification = "stub-verification-provider"
        self.last_ticket_decision = TicketDecision(
            status="SELECTED",
            option=None,
            quantity=2,
            matched_priority=None,
            fallback_used=False,
            trace=("priority[0] -> SELECTED",),
        )
        self.last_payment_result: PaymentResult | None = None

    async def detect_page_state(self, page: Any) -> PageState:
        self.calls.append("detect_page_state")
        if self.page_states:
            return self.page_states.pop(0)
        return PageState.PAYMENT_REQUIRED

    async def probe_page(self, page: Any, url: str | None = None) -> KKTIXPageKind:
        self.calls.append("probe" if url is None else "probe_navigate")
        return (
            self.probe_kinds.pop(0) if self.probe_kinds else KKTIXPageKind.REGISTRATION
        )

    async def navigate_to_event(self, page: Any, url: str) -> bool:
        self.calls.append("navigate")
        return True

    async def detect_sale_opened(self, page: Any, timeout_ms: int) -> bool:
        self.calls.append("detect_sale")
        return True

    async def read_registration_tickets(self, page: Any) -> list[TicketOption]:
        self.calls.append("read_registration_tickets")
        if self.registration_tickets is not None:
            return list(self.registration_tickets)
        return [
            TicketOption(
                index=0,
                name="全票",
                price=3200,
                available=True,
                remaining=None,
                status_text="全票/3200",
            )
        ]

    async def apply_ticket_decision(
        self, page: Any, decision: Any
    ) -> tuple[bool, str]:
        self.calls.append("apply_ticket_decision")
        self.last_ticket_decision = decision
        return (
            self.ticket_results.pop(0)
            if self.ticket_results
            else (True, REASON_SELECTED)
        )

    async def reset_ticket_quantities(self, page: Any) -> bool:
        self.calls.append("reset_ticket_quantities")
        return self.reset_ok

    async def dismiss_failure_modal(self, page: Any) -> bool:
        self.calls.append("dismiss_failure_modal")
        return self.dismiss_ok

    async def login(self, page: Any, username: str, secret_token: str) -> bool:
        self.calls.append("login")
        if self.on_login is not None:
            self.on_login()
        return self.login_ok

    async def navigate_to_login_from_guest_modal(self, page: Any) -> bool:
        self.calls.append("navigate_to_login_from_guest_modal")
        return True

    async def select_tickets(self, page: Any, preference: Any) -> tuple[bool, str]:
        self.calls.append("select_tickets")
        return (
            self.ticket_results.pop(0)
            if self.ticket_results
            else (False, REASON_SOLD_OUT)
        )

    async def handle_seat_selection(self, page: Any, preference: Any) -> bool:
        self.calls.append("seat")
        return self.seat_ok

    async def fill_contact_form(self, page: Any, profile: Any) -> bool:
        self.calls.append("form")
        return self.form_ok

    async def detect_verification(self, page: Any) -> bool:
        self.calls.append("detect_verification")
        return self.requires_verification

    async def handle_verification(self, page: Any) -> bool:
        self.calls.append("verification")
        return self.verification_results.pop(0) if self.verification_results else False

    async def submit_order(self, page: Any) -> bool:
        self.calls.append("submit_order")
        return self.submit_ok

    async def execute_payment(self, page: Any, profile: Any) -> PaymentResult:
        self.calls.append("payment")
        self.last_payment_result = PaymentResult(
            self.payment_outcome, "stub", {"submitted": False}
        )
        return self.last_payment_result


def make_spec(
    task_id: str = "task-orch",
    *,
    max_retries: int = 2,
    prices: tuple[int, ...] = (3200,),
    qualification_code: str | None = None,
    auto_login: bool = False,
) -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id=task_id,
        event_title="Demo",
        event_url="https://one.example.com/events/1",
        sale_start_at=BASE_WALL,
        ticket_preference=TicketPreference(
            quantity=2,
            priorities=[
                TicketPriority(price=p, priority=i + 1) for i, p in enumerate(prices)
            ],
            seat_preference=SeatPreference(),
        ),
        contact_profile=UserContactProfile(
            name="n", phone="0912345678", email="a@b.co"
        ),
        payment_method="mock",
        max_retries=max_retries,
        qualification_code=qualification_code,
        auto_login=auto_login,
    )


def build(
    tmp_path: Path,
    adapter: StubAdapter,
    *,
    spec: PurchaseTaskSpec | None = None,
    timeline_path: Path | None = None,
) -> tuple[PurchaseOrchestrator, StubScheduler, FakeBrowser, TimelineRecorder]:
    telemetry = TimelineRecorder()
    browser = FakeBrowser(FakePage("<div></div>"), tmp_path / "shots")
    scheduler = StubScheduler()
    orchestrator = PurchaseOrchestrator(
        spec or make_spec(),
        browser=browser,
        scheduler=scheduler,
        adapter=adapter,
        telemetry=telemetry,
        timeline_path=timeline_path,
    )
    return orchestrator, scheduler, browser, telemetry


# ------------------------------------------------------------------ 對應表


def test_payment_mapping_covers_every_outcome() -> None:
    assert set(PAYMENT_OUTCOME_EVENTS) == set(PaymentOutcome)


def test_payment_mapping_only_checkpoint_and_submitted_are_success() -> None:
    successes = {o for o, e in PAYMENT_OUTCOME_EVENTS.items() if e == "payment_success"}
    assert successes == {PaymentOutcome.SUBMITTED, PaymentOutcome.CHECKPOINT_REACHED}


def test_ticket_reason_mapping_covers_every_reason_code() -> None:
    """每個理由碼都必須恰好落在「可對應事件」或「致命」其中一邊。"""
    assert set(TICKET_REASON_EVENTS) | FATAL_TICKET_REASONS == set(ALL_TICKET_REASONS)
    assert not set(TICKET_REASON_EVENTS) & FATAL_TICKET_REASONS
    assert {REASON_NOT_REGISTRATION_PAGE} == FATAL_TICKET_REASONS
    assert {
        REASON_SELECTED,
        REASON_SOLD_OUT,
        REASON_NO_TICKET_UNITS,
        REASON_PLUS_BUTTON_MISSING,
        REASON_QUANTITY_MISMATCH,
        REASON_TERMS_NOT_ACCEPTED,
    } == set(TICKET_REASON_EVENTS)


# -------------------------------------------------------------------- 流程


async def test_happy_path_reaches_completed(tmp_path: Path) -> None:
    orchestrator, scheduler, _, _ = build(tmp_path, StubAdapter())
    report = await orchestrator.run()
    assert report.final_state == "COMPLETED"
    assert scheduler.started is True
    assert report.aborted is False


async def test_all_six_stages_are_registered(tmp_path: Path) -> None:
    orchestrator, scheduler, _, _ = build(tmp_path, StubAdapter())
    await orchestrator.run()
    assert set(scheduler.handlers) == set(ALL_STAGES)


async def test_event_sequence_on_happy_path(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter())
    await orchestrator.run()
    assert orchestrator._rt.events_sent == [
        "page_loaded",
        "ticket_reserved",
        "seat_confirmed",
        "form_submitted",
        "submit_payment",
        "payment_success",
    ]


async def test_adapter_call_order(tmp_path: Path) -> None:
    adapter = StubAdapter()
    orchestrator, _, _, _ = build(tmp_path, adapter)
    await orchestrator.run()
    assert adapter.calls == [
        "probe_navigate",
        "detect_sale",
        "detect_page_state",
        "read_registration_tickets",
        "apply_ticket_decision",
        "detect_page_state",
        "seat",
        "detect_page_state",
        "form",
        "detect_verification",
        "submit_order",
        "detect_page_state",
        "payment",
    ]


async def test_report_carries_sale_time_error_and_trace(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter())
    report = await orchestrator.run()
    assert report.sale_time_error_ms == TRIGGER_DRIFT_US / 1000.0
    assert report.ticket_trace and "SELECTED" in report.ticket_trace[0]
    assert (
        report.payment is not None
        and report.payment.outcome is PaymentOutcome.CHECKPOINT_REACHED
    )


async def test_report_lists_transition_screenshots(tmp_path: Path) -> None:
    orchestrator, _, browser, _ = build(tmp_path, StubAdapter())
    report = await orchestrator.run()
    assert len(browser.transitions) == len(report.screenshots)
    assert report.screenshots_expected == len(browser.transitions)
    assert all(name.startswith("task-orch_") for name in report.screenshots)


async def test_missing_screenshots_are_disclosed(tmp_path: Path) -> None:
    """截圖被逾時砍掉時要留痕，不能讓報告看起來很完整。"""
    orchestrator, _, browser, telemetry = build(tmp_path, StubAdapter())
    browser.write_screenshots = False
    report = await orchestrator.run()
    assert report.screenshots == ()
    assert report.screenshots_expected > 0
    incomplete = [e for e in telemetry.events() if e.name == "screenshots_incomplete"]
    assert incomplete[0].detail["expected"] == report.screenshots_expected
    assert incomplete[0].detail["written"] == 0


async def test_timeline_is_exported_when_path_given(tmp_path: Path) -> None:
    out = tmp_path / "timeline.json"
    orchestrator, _, _, _ = build(tmp_path, StubAdapter(), timeline_path=out)
    report = await orchestrator.run()
    assert report.timeline_path == out
    assert json.loads(out.read_text(encoding="utf-8"))


async def test_background_screenshots_are_drained_before_reporting(
    tmp_path: Path,
) -> None:
    """截圖是背景任務；不排空就產報告會少算最後幾張。"""
    orchestrator, _, browser, _ = build(tmp_path, StubAdapter())
    await orchestrator.run()
    assert browser.drained is True


async def test_browser_is_started_and_cdp_attached(tmp_path: Path) -> None:
    orchestrator, _, browser, _ = build(tmp_path, StubAdapter())
    await orchestrator.run()
    assert browser.started is True
    assert len(browser.attached) == 1


async def test_providers_default_to_the_adapter_instances(tmp_path: Path) -> None:
    adapter = StubAdapter()
    orchestrator, _, _, _ = build(tmp_path, adapter)
    assert orchestrator.payment == "stub-payment-provider"
    assert orchestrator.verification == "stub-verification-provider"


# ---------------------------------------------------------------- 分支路徑


async def test_sold_out_is_a_terminal_conclusion_not_a_failure(tmp_path: Path) -> None:
    adapter = StubAdapter(
        page_states=[PageState.TICKET_SELECTION],
        registration_tickets=[
            TicketOption(
                index=0,
                name="全票",
                price=3200,
                available=False,
                remaining=None,
                status_text="已售完",
            )
        ],
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert report.final_state == "SOLD_OUT"
    assert report.aborted is False
    assert "seat" not in adapter.calls


async def test_sold_out_report_discloses_the_real_failure_reason(
    tmp_path: Path,
) -> None:
    """卡在勾條款而收在 FAILED，不得與「真的售罄」長得一模一樣。"""
    adapter = StubAdapter(ticket_results=[(False, REASON_TERMS_NOT_ACCEPTED)])
    orchestrator, _, _, telemetry = build(
        tmp_path, adapter, spec=make_spec(prices=(3200,))
    )
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert report.ticket_failure_reasons == (REASON_TERMS_NOT_ACCEPTED,)
    failed = [e for e in telemetry.events() if e.name == "ticket_attempt_failed"]
    assert [e.detail["reason"] for e in failed] == [REASON_TERMS_NOT_ACCEPTED]
    assert failed[0].detail["attempt"] == 1


async def test_genuine_sold_out_is_still_reported_as_sold_out(tmp_path: Path) -> None:
    adapter = StubAdapter(
        page_states=[PageState.TICKET_SELECTION],
        registration_tickets=[
            TicketOption(
                index=0,
                name="全票",
                price=3200,
                available=False,
                remaining=None,
                status_text="已售完",
            )
        ],
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert report.ticket_failure_reasons == ()


async def test_successful_selection_records_no_failure_reason(tmp_path: Path) -> None:
    orchestrator, _, _, telemetry = build(tmp_path, StubAdapter())
    report = await orchestrator.run()
    assert report.ticket_failure_reasons == ()
    assert not [e for e in telemetry.events() if e.name == "ticket_attempt_failed"]


async def test_recoverable_ticket_failure_retries_next_priority(tmp_path: Path) -> None:
    tickets = [
        TicketOption(
            index=0,
            name="A 區",
            price=3200,
            available=True,
            remaining=None,
            status_text="A/3200",
        ),
        TicketOption(
            index=1,
            name="B 區",
            price=2400,
            available=True,
            remaining=None,
            status_text="B/2400",
        ),
    ]
    adapter = StubAdapter(
        page_states=[
            PageState.TICKET_SELECTION,
            PageState.FAILURE_MODAL,
            PageState.SEAT_SELECTION,
            PageState.FORM_FILLING,
            PageState.PAYMENT_REQUIRED,
        ],
        registration_tickets=tickets,
    )
    orchestrator, _, _, _ = build(
        tmp_path, adapter, spec=make_spec(prices=(3200, 2400))
    )
    report = await orchestrator.run()
    assert report.final_state == "COMPLETED"
    assert "ticket_fallback_reselected" in orchestrator._rt.events_sent
    assert "A 區" in orchestrator._rt.excluded_ticket_names
    assert orchestrator._rt.current_ticket_name == "B 區"
    assert "dismiss_failure_modal" in adapter.calls
    assert "reset_ticket_quantities" in adapter.calls


async def test_exhausted_priorities_end_in_sold_out(tmp_path: Path) -> None:
    tickets = [
        TicketOption(
            index=0,
            name="A 區",
            price=3200,
            available=True,
            remaining=None,
            status_text="A/3200",
        ),
    ]
    adapter = StubAdapter(
        page_states=[
            PageState.TICKET_SELECTION,
            PageState.FAILURE_MODAL,
        ],
        registration_tickets=tickets,
    )
    orchestrator, _, _, _ = build(tmp_path, adapter, spec=make_spec(prices=(3200,)))
    report = await orchestrator.run()
    assert report.final_state == "SOLD_OUT"
    assert "all_tickets_unavailable" in orchestrator._rt.events_sent


async def test_wrong_page_fails_closed_instead_of_reporting_sold_out(
    tmp_path: Path,
) -> None:
    adapter = StubAdapter(ticket_results=[(False, REASON_NOT_REGISTRATION_PAGE)])
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert report.final_state != "SOLD_OUT"
    assert REASON_NOT_REGISTRATION_PAGE in str(report.error)
    assert adapter.calls.count("apply_ticket_decision") == 1


async def test_session_gate_waits_for_the_human_then_proceeds(tmp_path: Path) -> None:
    """被人機驗證擋住、被導到登入頁，都只是「還沒好」，等人處理完就繼續。"""
    adapter = StubAdapter(
        probe_kinds=[
            KKTIXPageKind.CHALLENGE,
            KKTIXPageKind.LOGIN,
            KKTIXPageKind.REGISTRATION,
        ]
    )
    orchestrator, _, _, telemetry = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    seen: list[tuple[str, int]] = []

    async def gate(kind: str, attempt: int) -> None:
        seen.append((kind, attempt))

    orchestrator.session_gate = gate
    report = await orchestrator.run()
    assert report.final_state == "COMPLETED"
    assert seen == [("CHALLENGE", 1), ("LOGIN", 2)]
    probes = [e for e in telemetry.events() if e.name == "session_probe"]
    assert [p.detail["kind"] for p in probes] == ["CHALLENGE", "LOGIN", "REGISTRATION"]
    assert [e for e in telemetry.events() if e.name == "session_ready"]


async def test_session_gate_navigates_only_once(tmp_path: Path) -> None:
    """重判目前頁面就好；每次都重新導航是對站台不必要的輪詢。"""
    adapter = StubAdapter(probe_kinds=[KKTIXPageKind.LOGIN, KKTIXPageKind.REGISTRATION])
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    await orchestrator.run()
    assert adapter.calls.count("probe_navigate") == 1
    assert adapter.calls.count("probe") == 1


async def test_navigate_stage_reuses_the_page_the_gate_just_verified(
    tmp_path: Path,
) -> None:
    """就緒閘門剛證實這一頁下得了單；再導航一次只會把這個狀態沖掉。"""
    adapter = StubAdapter()
    orchestrator, _, _, telemetry = build(tmp_path, adapter)
    await orchestrator.run()
    assert "navigate" not in adapter.calls
    assert [e for e in telemetry.events() if e.name == "navigation_skipped"]


async def test_navigate_stage_navigates_when_the_page_left_the_verified_url(
    tmp_path: Path,
) -> None:
    """頁面已經離開閘門確認過的網址：這次非導不可，不得静默沿用。"""
    adapter = StubAdapter()
    orchestrator, _, browser, _ = build(tmp_path, adapter)
    orchestrator._rt.page = browser.page
    orchestrator._rt.session_ready_url = "https://registration.test/events/verified"
    browser.page.url = "https://registration.test/users/sign_in"
    await orchestrator._navigate_page(
        WarmupContext(
            task_id="t",
            stage=WarmupStage.NAVIGATE_PAGE,
            planned_local_at=BASE_WALL,
            fired_local_at=BASE_WALL,
            drift_us=0,
            time_reference=TimeReference(offset_ms=0.0),
        )
    )
    assert adapter.calls == ["navigate"]


async def test_session_gate_fails_closed_when_never_ready(tmp_path: Path) -> None:
    """逾時仍未就緒就中止，絕不帶著沒登入的頁面衝進開賣。"""
    adapter = StubAdapter(probe_kinds=[KKTIXPageKind.LOGIN] * 20)
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    orchestrator.session_gate_timeout_s = 0.0
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert "page not ready before sale: LOGIN" in str(report.error)
    assert "read_registration_tickets" not in adapter.calls


async def test_bot_check_fails_fast_even_with_a_visible_window(
    tmp_path: Path,
) -> None:
    """有視窗不等於過得了人機驗證：Playwright 自帶的瀏覽器開著視窗也一樣被擋。"""
    adapter = StubAdapter(probe_kinds=[KKTIXPageKind.CHALLENGE] * 20)
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    orchestrator.attended = True
    orchestrator.can_clear_bot_check = False
    orchestrator.session_gate_timeout_s = 600.0
    orchestrator.unattended_gate_grace_s = 0.0

    report = await orchestrator.run()

    assert report.final_state == "FAILED"
    error = str(report.error)
    # 必須把人導向借用模式，而不是叫他再開一次視窗。
    assert "--cdp-endpoint" in error
    assert "read_registration_tickets" not in adapter.calls


async def test_borrowed_browser_waits_for_the_human_through_the_bot_check(
    tmp_path: Path,
) -> None:
    """借用使用者自己的 Chrome 時，人機驗證是有機會被通過的——就該等他。"""
    adapter = StubAdapter(
        probe_kinds=[KKTIXPageKind.CHALLENGE] * 4 + [KKTIXPageKind.REGISTRATION]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    orchestrator.attended = True
    orchestrator.can_clear_bot_check = True
    orchestrator.unattended_gate_grace_s = 0.0
    orchestrator.session_gate_timeout_s = 600.0

    report = await orchestrator.run()

    assert report.final_state == "COMPLETED"


async def test_headless_gate_fails_fast_with_an_actionable_message(
    tmp_path: Path,
) -> None:
    """沒有可見視窗時，人機驗證不可能被通過——不要對著隱形視窗耗完整個等人預算。"""
    adapter = StubAdapter(probe_kinds=[KKTIXPageKind.CHALLENGE] * 20)
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    orchestrator.attended = False
    orchestrator.session_gate_timeout_s = 600.0
    orchestrator.unattended_gate_grace_s = 0.0

    report = await orchestrator.run()

    assert report.final_state == "FAILED"
    error = str(report.error)
    assert "headless" in error
    # 訊息必須告訴人下一步怎麼做，而不是只說「沒就緒」。
    assert "--no-headless" in error
    assert "read_registration_tickets" not in adapter.calls


async def test_headless_gate_still_allows_a_self_resolving_interstitial(
    tmp_path: Path,
) -> None:
    """Cloudflare 的過場有時自己會過；寬限期內恢復就該繼續，不該提早判死。"""
    adapter = StubAdapter(
        probe_kinds=[KKTIXPageKind.CHALLENGE, KKTIXPageKind.REGISTRATION]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    orchestrator.attended = False
    orchestrator.unattended_gate_grace_s = 60.0

    report = await orchestrator.run()

    assert report.final_state == "COMPLETED"


async def test_attended_gate_keeps_the_full_waiting_budget(tmp_path: Path) -> None:
    """有人看得到視窗時，等人的預算不得被無人模式的寬限期縮短。"""
    adapter = StubAdapter(
        probe_kinds=[KKTIXPageKind.CHALLENGE] * 5 + [KKTIXPageKind.REGISTRATION]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    orchestrator.session_gate_poll_s = 0.0
    orchestrator.attended = True
    orchestrator.unattended_gate_grace_s = 0.0
    orchestrator.session_gate_timeout_s = 600.0

    report = await orchestrator.run()

    assert report.final_state == "COMPLETED"


async def test_seat_failure_fails_closed(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter(seat_ok=False))
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert report.aborted is True
    assert "seat_conflict" in orchestrator._rt.events_sent


async def test_form_failure_fails_closed(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter(form_ok=False))
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert "fill_contact_form failed" in str(report.error)


async def test_submit_order_failure_fails_closed(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter(submit_ok=False))
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert "submit_order failed" in str(report.error)


async def test_verification_required_path(tmp_path: Path) -> None:
    adapter = StubAdapter(requires_verification=True, verification_results=[True])
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert "verification_passed" in orchestrator._rt.events_sent
    assert report.final_state == "COMPLETED"


async def test_verification_retries_before_succeeding(tmp_path: Path) -> None:
    adapter = StubAdapter(
        requires_verification=True, verification_results=[False, True]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert orchestrator._rt.events_sent.count("retry_verification") == 1
    assert report.final_state == "COMPLETED"


async def test_verification_exhaustion_fails_closed(tmp_path: Path) -> None:
    adapter = StubAdapter(
        requires_verification=True, verification_results=[False, False, False]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter, spec=make_spec(max_retries=1))
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert adapter.calls.count("verification") == 2


async def test_run_without_page_raises_step_error(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter())
    with pytest.raises(PurchaseStepError):
        orchestrator._require_page()


@pytest.mark.parametrize("outcome", list(PaymentOutcome))
async def test_every_payment_outcome_lands_in_the_mapped_state(
    tmp_path: Path, outcome: PaymentOutcome
) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter(payment_outcome=outcome))
    report = await orchestrator.run()
    expected = (
        "COMPLETED"
        if PAYMENT_OUTCOME_EVENTS[outcome] == "payment_success"
        else "FAILED"
    )
    assert report.final_state == expected
    assert orchestrator._rt.events_sent[-1] == PAYMENT_OUTCOME_EVENTS[outcome]


async def test_single_payment_lock_prevents_duplicate_payment(tmp_path: Path) -> None:
    """單次付款鎖：生命週期至多一次，不可重試。"""
    adapter = StubAdapter()
    orchestrator, _, _, _ = build(tmp_path, adapter)
    page = FakePage("<div></div>")
    orchestrator._rt.page = page
    from fsm.machine import PurchaseWorkflow

    orchestrator.fsm = PurchaseWorkflow(orchestrator.spec, orchestrator.telemetry)
    orchestrator.fsm.sync_to_state("PAYMENT_REQUIRED", "form_submitted")
    await orchestrator._handle_payment_required(page)
    assert orchestrator._rt.payment_attempted is True
    with pytest.raises(PurchaseStepError, match="payment already attempted"):
        await orchestrator._handle_payment_required(page)


QUALIFICATION_CODE_HTML = (
    "<div id='registrationsNewApp'><div class='code-input'>"
    "<input type='text' ng-model='code'>"
    "<button type='button' class='btn' ng-click='checkCode()'>送出</button>"
    "</div></div>"
)


async def test_qualification_code_flow(tmp_path: Path) -> None:
    """專屬邀請碼自動解析並填入。"""
    spec = make_spec(qualification_code="VIP2026")
    adapter = StubAdapter(
        page_states=[
            PageState.QUALIFICATION_CODE,
            PageState.TICKET_SELECTION,
            PageState.SEAT_SELECTION,
            PageState.FORM_FILLING,
            PageState.PAYMENT_REQUIRED,
        ]
    )
    orchestrator, _, browser, telemetry = build(tmp_path, adapter, spec=spec)
    browser.page = FakePage(QUALIFICATION_CODE_HTML)
    report = await orchestrator.run()
    assert report.final_state == "COMPLETED"
    assert orchestrator._rt.qualification_handled is True
    assert any(e.name == "qualification_code_submitted" for e in telemetry.events())
    assert browser.page.fills == [("div.code-input input[type='text']", "VIP2026")]


async def test_qualification_code_missing_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """無會員碼時 fail-closed 中止。"""
    monkeypatch.delenv("AUTO_TICKET_MEMBER_CODE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    spec = make_spec(qualification_code="")
    adapter = StubAdapter(
        page_states=[PageState.QUALIFICATION_CODE]
    )
    orchestrator, _, browser, telemetry = build(tmp_path, adapter, spec=spec)
    browser.page = FakePage(QUALIFICATION_CODE_HTML)
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert any(e.name == "qualification_code_missing" for e in telemetry.events())


async def test_auto_login_screenshot_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自動登入防截圖屏障：登入期間卸載 hook，且機密不落地。"""
    monkeypatch.setenv("AUTO_TICKET_KKTIX_USERNAME", "testuser@example.com")
    monkeypatch.setenv("AUTO_TICKET_KKTIX_PASSWORD", "supersecret123")
    spec = make_spec(auto_login=True)

    adapter = StubAdapter(
        page_states=[
            PageState.GUEST_MODAL,
            PageState.TICKET_SELECTION,
            PageState.SEAT_SELECTION,
            PageState.FORM_FILLING,
            PageState.PAYMENT_REQUIRED,
        ]
    )
    timeline_file = tmp_path / "timeline.json"
    orchestrator, _, browser, telemetry = build(
        tmp_path, adapter, spec=spec, timeline_path=timeline_file
    )

    hook_during_login: Any = "UNSET"

    def check_hook() -> None:
        nonlocal hook_during_login
        hook_during_login = orchestrator._rt.screenshot_hook

    adapter.on_login = check_hook

    report = await orchestrator.run()
    assert report.final_state == "COMPLETED"
    assert hook_during_login is None  # 屏障生效：登入中 hook 為 None
    assert orchestrator._rt.screenshot_hook is not None  # 登入後恢復
    assert "login" in adapter.calls
    assert "navigate_to_login_from_guest_modal" in adapter.calls

    # 驗證 timeline 絕無帳號密碼明文
    exported_text = timeline_file.read_text(encoding="utf-8")
    assert "testuser@example.com" not in exported_text
    assert "supersecret123" not in exported_text


async def test_reset_tickets_failure_fails_closed(tmp_path: Path) -> None:
    """彈窗關閉後若數量歸零失敗，必須立即中止，避免多票送出。"""
    tickets = [
        TicketOption(
            index=0,
            name="A 區",
            price=3200,
            available=True,
            remaining=None,
            status_text="A/3200",
        ),
    ]
    adapter = StubAdapter(
        page_states=[
            PageState.TICKET_SELECTION,
            PageState.FAILURE_MODAL,
        ],
        registration_tickets=tickets,
        reset_ok=False,
    )
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert "Failed to reset quantities to zero" in str(report.error)
