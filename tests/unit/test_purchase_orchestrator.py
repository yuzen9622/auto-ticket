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
)
from domain.preference import SeatPreference, TicketPreference, TicketPriority
from domain.task import PurchaseTaskSpec, UserContactProfile
from purchase.handlers import (
    FATAL_TICKET_REASONS,
    TICKET_REASON_EVENTS,
    PurchaseStepError,
)
from purchase.orchestrator import PAYMENT_OUTCOME_EVENTS, PurchaseOrchestrator
from scheduler.scheduler import StageOutcome, WarmupContext, WarmupStage
from strategy.ticket_strategy import TicketDecision
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

    async def schedule(self, spec: PurchaseTaskSpec, fsm: Any = None, **kwargs: Any) -> StubPlan:
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
                        task_id=spec.task_id, stage=stage, planned_local_at=BASE_WALL,
                        fired_local_at=BASE_WALL, drift_us=drift, time_reference=None,
                    )
                )
            except Exception as exc:
                self.plan.outcomes.append(StageOutcome(stage, "FAILED", drift, error=exc))
                self.plan.aborted = True
                if fsm.can_send("abort_failed"):
                    fsm.send("abort_failed")
                return self.plan
            self.plan.outcomes.append(StageOutcome(stage, "OK", drift))
            if stage is WarmupStage.CHECK_SESSION:
                fsm.send("session_ready")
        return self.plan

    async def wait_until_finished(self, task_id: str, timeout: float | None = None) -> bool:
        return True


class StubAdapter:
    name = "stub"

    def __init__(
        self,
        *,
        ticket_results: list[tuple[bool, str]] | None = None,
        seat_ok: bool = True,
        form_ok: bool = True,
        requires_verification: bool = False,
        verification_results: list[bool] | None = None,
        submit_ok: bool = True,
        payment_outcome: PaymentOutcome = PaymentOutcome.CHECKPOINT_REACHED,
    ) -> None:
        self.ticket_results = list(ticket_results or [(True, REASON_SELECTED)])
        self.seat_ok = seat_ok
        self.form_ok = form_ok
        self.requires_verification = requires_verification
        self.verification_results = list(verification_results or [True])
        self.submit_ok = submit_ok
        self.payment_outcome = payment_outcome
        self.calls: list[str] = []
        self.payment = "stub-payment-provider"
        self.verification = "stub-verification-provider"
        self.last_ticket_decision = TicketDecision(
            status="SELECTED", option=None, quantity=2, matched_priority=None,
            fallback_used=False, trace=("priority[0] -> SELECTED",),
        )
        self.last_payment_result: PaymentResult | None = None

    async def navigate_to_event(self, page: Any, url: str) -> bool:
        self.calls.append("navigate")
        return True

    async def detect_sale_opened(self, page: Any, timeout_ms: int) -> bool:
        self.calls.append("detect_sale")
        return True

    async def select_tickets(self, page: Any, preference: Any) -> tuple[bool, str]:
        self.calls.append("select_tickets")
        return self.ticket_results.pop(0) if self.ticket_results else (False, REASON_SOLD_OUT)

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
        self.last_payment_result = PaymentResult(self.payment_outcome, "stub", {"submitted": False})
        return self.last_payment_result


def make_spec(
    task_id: str = "task-orch", *, max_retries: int = 2, prices: tuple[int, ...] = (3200,)
) -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id=task_id,
        event_title="Demo",
        event_url="https://one.example.com/events/1",
        sale_start_at=BASE_WALL,
        ticket_preference=TicketPreference(
            quantity=2,
            priorities=[TicketPriority(price=p, priority=i + 1) for i, p in enumerate(prices)],
            seat_preference=SeatPreference(),
        ),
        contact_profile=UserContactProfile(name="n", phone="0912345678", email="a@b.co"),
        payment_method="mock",
        max_retries=max_retries,
    )


def build(
    tmp_path: Path, adapter: StubAdapter, *, spec: PurchaseTaskSpec | None = None,
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
    assert FATAL_TICKET_REASONS == {REASON_NOT_REGISTRATION_PAGE}
    assert {REASON_SELECTED, REASON_SOLD_OUT, REASON_NO_TICKET_UNITS,
            REASON_PLUS_BUTTON_MISSING, REASON_QUANTITY_MISMATCH,
            REASON_TERMS_NOT_ACCEPTED} == set(TICKET_REASON_EVENTS)


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
        "page_loaded", "ticket_reserved", "seat_confirmed",
        "form_submitted", "submit_payment", "payment_success",
    ]


async def test_adapter_call_order(tmp_path: Path) -> None:
    adapter = StubAdapter()
    orchestrator, _, _, _ = build(tmp_path, adapter)
    await orchestrator.run()
    assert adapter.calls == [
        "navigate", "detect_sale", "select_tickets", "seat", "form",
        "detect_verification", "submit_order", "payment",
    ]


async def test_report_carries_sale_time_error_and_trace(tmp_path: Path) -> None:
    orchestrator, _, _, _ = build(tmp_path, StubAdapter())
    report = await orchestrator.run()
    assert report.sale_time_error_ms == TRIGGER_DRIFT_US / 1000.0
    assert report.ticket_trace == ("priority[0] -> SELECTED",)
    assert report.payment is not None and report.payment.outcome is PaymentOutcome.CHECKPOINT_REACHED


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


async def test_background_screenshots_are_drained_before_reporting(tmp_path: Path) -> None:
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
    adapter = StubAdapter(ticket_results=[(False, REASON_SOLD_OUT)])
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert report.final_state == "SOLD_OUT"
    assert report.aborted is False
    assert "seat" not in adapter.calls


async def test_recoverable_ticket_failure_retries_next_priority(tmp_path: Path) -> None:
    adapter = StubAdapter(
        ticket_results=[(False, REASON_QUANTITY_MISMATCH), (True, REASON_SELECTED)]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter, spec=make_spec(prices=(3200, 2400)))
    report = await orchestrator.run()
    assert orchestrator._rt.events_sent[:3] == [
        "page_loaded", "retry_fallback_ticket", "ticket_reserved"
    ]
    assert report.final_state == "COMPLETED"


async def test_exhausted_priorities_end_in_sold_out(tmp_path: Path) -> None:
    adapter = StubAdapter(
        ticket_results=[(False, REASON_QUANTITY_MISMATCH), (False, REASON_QUANTITY_MISMATCH)]
    )
    orchestrator, _, _, _ = build(tmp_path, adapter, spec=make_spec(prices=(3200,)))
    report = await orchestrator.run()
    assert report.final_state == "SOLD_OUT"
    assert adapter.calls.count("select_tickets") == 1


async def test_wrong_page_fails_closed_instead_of_reporting_sold_out(tmp_path: Path) -> None:
    adapter = StubAdapter(ticket_results=[(False, REASON_NOT_REGISTRATION_PAGE)])
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert report.final_state == "FAILED"
    assert report.final_state != "SOLD_OUT"
    assert REASON_NOT_REGISTRATION_PAGE in str(report.error)
    assert adapter.calls.count("select_tickets") == 1


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
    adapter = StubAdapter(requires_verification=True, verification_results=[False, True])
    orchestrator, _, _, _ = build(tmp_path, adapter)
    report = await orchestrator.run()
    assert orchestrator._rt.events_sent.count("retry_verification") == 1
    assert report.final_state == "COMPLETED"


async def test_verification_exhaustion_fails_closed(tmp_path: Path) -> None:
    adapter = StubAdapter(requires_verification=True, verification_results=[False, False, False])
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
    expected = "COMPLETED" if PAYMENT_OUTCOME_EVENTS[outcome] == "payment_success" else "FAILED"
    assert report.final_state == expected
    assert orchestrator._rt.events_sent[-1] == PAYMENT_OUTCOME_EVENTS[outcome]
