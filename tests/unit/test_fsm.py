"""購票狀態機：形狀、33 條轉移路徑全覆蓋、條件分流與終態防護。"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import pytest

from domain.preference import TicketPreference, TicketPriority
from domain.task import CreditCardProfile, PurchaseTaskSpec, UserContactProfile
from fsm.machine import PurchaseWorkflow
from fsm.states import FINAL_STATES, PurchaseEvent, PurchaseState
from telemetry.timeline import TimelineEventType, TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401

SALE_AT = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def make_spec(*, priorities: int = 2, max_retries: int = 3) -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id="task-1",
        event_title="Demo",
        event_url="https://tickets.example.com/events/1",
        sale_start_at=SALE_AT,
        ticket_preference=TicketPreference(
            priorities=[
                TicketPriority(price=1000 - i, priority=i + 1) for i in range(priorities)
            ]
        ),
        contact_profile=UserContactProfile(name="n", phone="0912345678", email="a@b.co"),
        payment_profile=CreditCardProfile(
            card_number="4111111111111111",
            expiry_month="01",
            expiry_year="30",
            cvv="123",
            cardholder_name="N",
        ),
        max_retries=max_retries,
    )


def declared_transitions() -> set[tuple[str, str, str]]:
    return {
        (t.source.id, str(e), t.target.id)
        for state in PurchaseWorkflow.states
        for t in state.transitions
        for e in t.events
    }


class Trace:
    def __init__(self) -> None:
        self.triples: list[tuple[str, str, str]] = []

    def __call__(self, from_id: str, to_id: str, event_id: str) -> None:
        self.triples.append((from_id, event_id, to_id))


def run_flow(events: list[str], *, priorities: int = 2, max_retries: int = 3,
             requires_verification: bool = False) -> tuple[PurchaseWorkflow, Trace]:
    trace = Trace()
    wf = PurchaseWorkflow(
        make_spec(priorities=priorities, max_retries=max_retries),
        on_transition_hook=trace,
    )
    wf.set_requires_verification(requires_verification)
    for name in events:
        getattr(wf, name)()
    return wf, trace


# ------------------------------------------------------------------ 形狀
def test_state_event_and_final_counts() -> None:
    assert len(PurchaseState) == 14
    assert len(PurchaseEvent) == 18
    assert len(FINAL_STATES) == 4
    assert {s.value for s in FINAL_STATES} == {"COMPLETED", "SOLD_OUT", "TIMEOUT", "FAILED"}


def test_machine_declares_exactly_33_transitions() -> None:
    assert PurchaseWorkflow.transition_count() == 33
    assert len(declared_transitions()) == 33


def test_machine_states_match_state_enum() -> None:
    assert {s.id for s in PurchaseWorkflow.states} == {s.value for s in PurchaseState}


def test_initial_state_is_idle() -> None:
    wf = PurchaseWorkflow(make_spec())
    assert wf.current_state_id == "IDLE"


# ------------------------------------------------------------------ 覆蓋
def test_all_33_transitions_are_exercised() -> None:
    covered: set[tuple[str, str, str]] = set()

    flows: list[dict[str, object]] = [
        # 完整成功路徑（含免驗證分流與付款成功）
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_confirmed", "form_submitted",
                    "submit_payment", "payment_success"]},
        # 驗證碼分流：重試一次 -> 通過 -> 付款失敗
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_confirmed", "form_submitted",
                    "retry_verification", "verification_passed",
                    "submit_payment", "payment_declined"],
         "requires_verification": True},
        # 驗證碼重試耗盡
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_confirmed", "form_submitted",
                    "retry_verification"],
         "requires_verification": True, "max_retries": 0},
        # 驗證碼直接失敗
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_confirmed", "form_submitted",
                    "verification_failed"],
         "requires_verification": True},
        # 座位衝突重試 -> 回票種選擇 -> 降級留在原狀態 -> 全數售罄
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_conflict", "retry_fallback_ticket",
                    "all_tickets_unavailable"]},
        # 座位重試耗盡
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_conflict"],
         "max_retries": 0},
        # 票種降級耗盡 -> SOLD_OUT
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "retry_fallback_ticket"],
         "priorities": 1},
        # 三條 abort_timeout
        {"events": ["prepare_session", "session_ready", "sale_triggered", "abort_timeout"]},
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "abort_timeout"]},
        {"events": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                    "ticket_reserved", "seat_confirmed", "abort_timeout"]},
    ]
    # 10 個非終態的 abort_failed
    abort_prefixes: dict[str, list[str]] = {
        "IDLE": [],
        "PREPARING": ["prepare_session"],
        "WAITING_FOR_SALE": ["prepare_session", "session_ready"],
        "SALE_OPEN": ["prepare_session", "session_ready", "sale_triggered"],
        "TICKET_SELECTION": ["prepare_session", "session_ready", "sale_triggered", "page_loaded"],
        "SEAT_SELECTION": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                           "ticket_reserved"],
        "FORM_FILLING": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                         "ticket_reserved", "seat_confirmed"],
        "VERIFICATION_REQUIRED": ["prepare_session", "session_ready", "sale_triggered",
                                  "page_loaded", "ticket_reserved", "seat_confirmed",
                                  "form_submitted"],
        "PAYMENT_REQUIRED": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                             "ticket_reserved", "seat_confirmed", "form_submitted"],
        "PAYMENT_PROCESSING": ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
                               "ticket_reserved", "seat_confirmed", "form_submitted",
                               "submit_payment"],
    }
    for state_id, prefix in abort_prefixes.items():
        flows.append({
            "events": [*prefix, "abort_failed"],
            "requires_verification": state_id == "VERIFICATION_REQUIRED",
        })

    for flow in flows:
        _, trace = run_flow(**flow)  # type: ignore[arg-type]
        covered.update(trace.triples)

    missing = declared_transitions() - covered
    assert not missing, f"未覆蓋的轉移: {sorted(missing)}"
    assert covered == declared_transitions()


# ------------------------------------------------------------------ 條件分流
def test_ticket_fallback_stays_when_next_priority_exists() -> None:
    wf, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "retry_fallback_ticket"],
        priorities=2,
    )
    assert wf.current_state_id == "TICKET_SELECTION"
    assert wf.current_priority_index == 1


def test_ticket_fallback_exhausted_goes_sold_out() -> None:
    wf, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "retry_fallback_ticket"],
        priorities=1,
    )
    assert wf.current_state_id == "SOLD_OUT"


def test_seat_conflict_retry_then_exhaustion() -> None:
    wf, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "ticket_reserved", "seat_conflict"],
        max_retries=1,
    )
    assert wf.current_state_id == "TICKET_SELECTION"
    assert wf.seat_retry_count == 1

    wf2, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "ticket_reserved", "seat_conflict"],
        max_retries=0,
    )
    assert wf2.current_state_id == "FAILED"


def test_verification_retry_then_exhaustion() -> None:
    wf, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "ticket_reserved", "seat_confirmed", "form_submitted", "retry_verification"],
        requires_verification=True,
        max_retries=2,
    )
    assert wf.current_state_id == "VERIFICATION_REQUIRED"
    assert wf.verification_retry_count == 1

    wf2, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "ticket_reserved", "seat_confirmed", "form_submitted", "retry_verification"],
        requires_verification=True,
        max_retries=0,
    )
    assert wf2.current_state_id == "FAILED"


def test_form_submitted_branches_on_verification_flag() -> None:
    with_verify, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "ticket_reserved", "seat_confirmed", "form_submitted"],
        requires_verification=True,
    )
    assert with_verify.current_state_id == "VERIFICATION_REQUIRED"

    without_verify, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
         "ticket_reserved", "seat_confirmed", "form_submitted"],
    )
    assert without_verify.current_state_id == "PAYMENT_REQUIRED"


# ------------------------------------------------ [ABORT-LEGALITY-GATE]
def test_abort_failed_covers_every_non_final_state() -> None:
    non_final = {s.value for s in PurchaseState} - {s.value for s in FINAL_STATES}
    assert PurchaseWorkflow.transition_sources("abort_failed") == non_final
    assert len(non_final) == 10


@pytest.mark.parametrize(
    "prefix",
    [
        [],
        ["prepare_session"],
        ["prepare_session", "session_ready"],
        ["prepare_session", "session_ready", "sale_triggered"],
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded"],
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded", "ticket_reserved"],
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded", "ticket_reserved",
         "seat_confirmed"],
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded", "ticket_reserved",
         "seat_confirmed", "form_submitted"],
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded", "ticket_reserved",
         "seat_confirmed", "form_submitted", "submit_payment"],
    ],
)
def test_abort_failed_is_legal_from_any_reached_state(prefix: list[str]) -> None:
    wf, _ = run_flow(prefix)
    assert wf.can_send("abort_failed") is True
    wf.abort_failed()
    assert wf.current_state_id == "FAILED"


def test_abort_failed_is_legal_from_verification_required() -> None:
    wf, _ = run_flow(
        ["prepare_session", "session_ready", "sale_triggered", "page_loaded", "ticket_reserved",
         "seat_confirmed", "form_submitted"],
        requires_verification=True,
    )
    assert wf.current_state_id == "VERIFICATION_REQUIRED"
    assert wf.can_send("abort_failed") is True
    wf.abort_failed()
    assert wf.current_state_id == "FAILED"


# ------------------------------------------------------------------ can_send
def test_can_send_reflects_allowed_events_only() -> None:
    wf = PurchaseWorkflow(make_spec())
    assert wf.can_send("prepare_session") is True
    assert wf.can_send("sale_triggered") is False
    assert wf.can_send("no_such_event") is False


def test_can_send_is_false_in_every_final_state() -> None:
    wf, _ = run_flow(["prepare_session", "session_ready", "sale_triggered", "abort_timeout"])
    assert wf.current_state_id == "TIMEOUT"
    assert wf.can_send("abort_failed") is False
    assert wf.can_send("prepare_session") is False


def test_final_state_guard_blocks_further_transitions() -> None:
    wf, _ = run_flow(["prepare_session", "abort_failed"])
    assert wf.current_state_id == "FAILED"
    with pytest.raises(Exception):
        wf.session_ready()


# ------------------------------------------------------------------ telemetry
def test_transitions_are_recorded_to_timeline() -> None:
    rec = TimelineRecorder()
    wf = PurchaseWorkflow(make_spec(), rec)
    wf.prepare_session()
    wf.session_ready()
    events = rec.events_of(TimelineEventType.TRANSITION)
    assert [e.detail["event"] for e in events] == ["prepare_session", "session_ready"]
    assert events[0].detail["from_state"] == "IDLE"
    assert events[1].detail["to_state"] == "WAITING_FOR_SALE"


def test_transition_hook_receives_every_transition() -> None:
    _, trace = run_flow(["prepare_session", "session_ready", "sale_triggered"])
    assert trace.triples == [
        ("IDLE", "prepare_session", "PREPARING"),
        ("PREPARING", "session_ready", "WAITING_FOR_SALE"),
        ("WAITING_FOR_SALE", "sale_triggered", "SALE_OPEN"),
    ]


def test_no_deprecation_warning_during_full_flow() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        wf, _ = run_flow(
            ["prepare_session", "session_ready", "sale_triggered", "page_loaded",
             "ticket_reserved", "seat_confirmed", "form_submitted", "submit_payment",
             "payment_success"]
        )
        assert wf.current_state_id == "COMPLETED"
        assert wf.can_send("abort_failed") is False
