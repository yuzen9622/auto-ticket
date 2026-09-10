from __future__ import annotations

from typing import Any

import pytest

from adapters.payment import MockPaymentProvider, PaymentOutcome, PaymentResult
from adapters.ticketing.kktix.adapter import (
    REASON_NO_TICKET_UNITS,
    REASON_NOT_REGISTRATION_PAGE,
    REASON_PLUS_BUTTON_MISSING,
    REASON_QUANTITY_MISMATCH,
    REASON_SELECTED,
    REASON_SOLD_OUT,
    REASON_TERMS_NOT_ACCEPTED,
    CloudflareChallengeError,
    KKTIXAdapter,
    KKTIXPageKind,
)
from adapters.ticketing.kktix.dom import (
    SELECTOR_FALLBACK_MARK,
    candidate_selectors,
    contains_cloudflare_challenge,
    first_visible,
    ng_click,
    ng_fill,
)
from adapters.verification.base import (
    VerificationChallenge,
    VerificationResult,
)
from domain.preference import SeatPreference, TicketPreference, TicketPriority
from domain.task import AttendeeProfile, UserContactProfile
from strategy.seat_strategy import DOWNGRADE_MARK
from telemetry.timeline import TimelineRecorder
from tests.fake_page import FakePage
from tests.netguard import netguard_autouse  # noqa: F401

CLOUDFLARE_HTML = "<html><body><h1>正在執行安全驗證</h1></body></html>"


class StubVerification:
    name = "stub"

    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        self.calls: list[VerificationChallenge] = []

    async def solve(self, challenge: VerificationChallenge) -> VerificationResult:
        self.calls.append(challenge)
        return self.result


class StubPayment:
    name = "stub_payment"

    def __init__(self, outcome: PaymentOutcome = PaymentOutcome.CHECKPOINT_REACHED) -> None:
        self.outcome = outcome
        self.calls: list[Any] = []

    async def pay(self, page: Any, profile: Any) -> PaymentResult:
        self.calls.append(profile)
        return PaymentResult(self.outcome, self.name, {"submitted": False})


@pytest.fixture
def telemetry() -> TimelineRecorder:
    return TimelineRecorder()


def make_adapter(
    telemetry: TimelineRecorder,
    *,
    payment: Any = None,
    verification: Any = None,
    attendees: tuple[AttendeeProfile, ...] = (),
    screenshot: Any = None,
) -> KKTIXAdapter:
    return KKTIXAdapter(
        telemetry=telemetry,
        payment=payment or MockPaymentProvider(),
        verification=verification,
        attendees=attendees,
        timeout_ms=20,
        screenshot=screenshot,
    )


def preference(price: int = 3200, *, quantity: int = 2, fallback: bool = False) -> TicketPreference:
    return TicketPreference(
        quantity=quantity,
        priorities=[TicketPriority(price=price)],
        seat_preference=SeatPreference(),
        fallback_to_any=fallback,
    )


def marks(telemetry: TimelineRecorder, name: str) -> list[Any]:
    return [e for e in telemetry.events() if e.name == name]


# ------------------------------------------------------------------- dom


def test_candidate_selectors_normalizes_both_shapes() -> None:
    assert candidate_selectors("a, b") == ("a, b",)
    assert candidate_selectors(["a", "b"]) == ("a", "b")


async def test_first_visible_returns_the_first_matching_candidate(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div><button class='plus'>+</button></div>")
    locator = await first_visible(page, ["button.plus", "button.minus"], telemetry=telemetry, timeout_ms=20)
    assert locator is not None
    assert marks(telemetry, SELECTOR_FALLBACK_MARK) == []


async def test_first_visible_records_rank_when_falling_back(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div><button class='minus'>-</button></div>")
    locator = await first_visible(
        page, ["button.plus", "button.minus"], telemetry=telemetry, timeout_ms=20, field="qty"
    )
    assert locator is not None
    fallback = marks(telemetry, SELECTOR_FALLBACK_MARK)
    assert len(fallback) == 1
    assert fallback[0].detail["rank"] == 2
    assert fallback[0].detail["field"] == "qty"


async def test_first_visible_returns_none_instead_of_raising(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div></div>")
    assert await first_visible(page, ["button.plus"], telemetry=telemetry, timeout_ms=20) is None


async def test_ng_click_dispatches_angular_events() -> None:
    page = FakePage("<div><button class='plus'>+</button></div>")
    locator = page.locator("button.plus").first
    await ng_click(page, locator)
    assert page.clicks == ["button.plus"]
    assert page.dispatches and "dispatchEvent" in page.dispatches[0][1]


async def test_ng_fill_sets_value_and_dispatches() -> None:
    page = FakePage("<div><input id='x'></div>")
    locator = page.locator("input#x").first
    await ng_fill(page, locator, "hello")
    assert await locator.input_value() == "hello"
    assert page.dispatches[0][0] == "input"


def test_cloudflare_detection_is_case_insensitive() -> None:
    assert contains_cloudflare_challenge("Just A Moment") == "just a moment"
    assert contains_cloudflare_challenge("正在執行安全驗證") == "正在執行安全驗證"
    assert contains_cloudflare_challenge("一切正常") is None


# --------------------------------------------------------------- 導航／偵測


async def test_navigate_to_event_visits_url(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    adapter = make_adapter(telemetry)
    assert await adapter.navigate_to_event(page, "https://reg.test/e/1") is True
    assert page.goto_urls == ["https://reg.test/e/1"]
    assert marks(telemetry, "navigated")


async def test_cloudflare_challenge_aborts_and_screenshots(telemetry: TimelineRecorder) -> None:
    shots: list[str] = []

    async def screenshot(name: str) -> None:
        shots.append(name)

    adapter = make_adapter(telemetry, screenshot=screenshot)
    with pytest.raises(CloudflareChallengeError):
        await adapter.navigate_to_event(FakePage(CLOUDFLARE_HTML), "https://reg.test/e/1")
    assert shots == ["cloudflare_challenge_navigate"]
    assert marks(telemetry, "cloudflare_challenge")


async def test_cloudflare_guard_also_applies_to_ticket_selection(telemetry: TimelineRecorder) -> None:
    adapter = make_adapter(telemetry)
    with pytest.raises(CloudflareChallengeError):
        await adapter.select_tickets(FakePage(CLOUDFLARE_HTML), preference())


async def test_detect_sale_opened_true_on_registration_page(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert await make_adapter(telemetry).detect_sale_opened(page, 20) is True


async def test_detect_sale_opened_false_without_registration_app(telemetry: TimelineRecorder) -> None:
    assert await make_adapter(telemetry).detect_sale_opened(FakePage("<div></div>"), 20) is False


# ------------------------------------------------------------------ 頁面判別


async def test_detect_page_kind_registration(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert await make_adapter(telemetry).detect_page_kind(page) is KKTIXPageKind.REGISTRATION


async def test_detect_page_kind_event_main_page(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_event_page.html")
    assert await make_adapter(telemetry).detect_page_kind(page) is KKTIXPageKind.EVENT


async def test_detect_page_kind_order(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await make_adapter(telemetry).detect_page_kind(page) is KKTIXPageKind.ORDER


async def test_detect_page_kind_unknown(telemetry: TimelineRecorder) -> None:
    """被踢回登入頁必須和訂單頁分得開，否則研究資料會把兩者混為一談。"""
    page = FakePage("<div>請先登入</div>")
    assert await make_adapter(telemetry).detect_page_kind(page) is KKTIXPageKind.UNKNOWN


async def test_event_page_tickets_are_read_from_the_table(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_event_page.html")
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert [o.name for o in options] == ["預售全區站席", "搖滾區站席", "學生優惠票"]
    assert [o.price for o in options] == [2800, 3800, 0]


async def test_event_page_does_not_invent_stock(telemetry: TimelineRecorder) -> None:
    """主頁看不到庫存：remaining 一律 None，不得臆造；只有明寫售完才是不可選。"""
    page = FakePage.from_fixture("kktix_event_page.html")
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert all(o.remaining is None for o in options)
    assert all(o.status_text for o in options)
    by_name = {o.name: o for o in options}
    assert by_name["搖滾區站席"].available is False  # 狀態欄明寫「已售完」
    assert by_name["預售全區站席"].available is True
    # 「尚未開賣」不等於售完；主頁快照如實照抄狀態字串，不替它判斷能不能買
    assert by_name["學生優惠票"].available is True
    assert "尚未開賣" in by_name["學生優惠票"].status_text


async def test_event_page_header_row_is_skipped(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_event_page.html")
    rows = await page.locator("div.tickets table tbody tr").count()
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert rows == 4
    assert len(options) == 3


async def test_selecting_on_the_event_page_is_not_reported_as_sold_out(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_event_page.html")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference(2800))
    assert (ok, reason) == (False, REASON_NOT_REGISTRATION_PAGE)
    assert marks(telemetry, "wrong_page_for_selection")[0].detail["page_kind"] == "EVENT"


async def test_selecting_on_a_login_redirect_is_not_reported_as_sold_out(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div>請先登入 KKTIX 帳號</div>")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_NOT_REGISTRATION_PAGE)
    assert marks(telemetry, "wrong_page_for_selection")[0].detail["page_kind"] == "UNKNOWN"


# ------------------------------------------------------------------ 票種讀取


async def test_read_ticket_options_parses_snapshot(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert [o.name for o in options] == ["全票 A 區", "全票 B 區", "搖滾站區"]
    assert [o.price for o in options] == [3200, 2400, 4800]
    assert [o.remaining for o in options] == [12, 3, None]


async def test_read_ticket_options_marks_sold_out_unit_unavailable(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert [o.available for o in options] == [True, True, False]
    assert options[2].status_text == "已售完"


async def test_ticket_unit_selector_fallback_is_recorded(telemetry: TimelineRecorder) -> None:
    page = FakePage(
        "<div id='registrationsNewApp'><table><tbody>"
        "<tr id='ticket_9001'><td class='name'>單一票種</td><td class='price'>NT$ 500</td>"
        "<td class='status'>剩餘 9 張</td>"
        "<td><button class='btn-default plus' ng-click='quantityBtnClick(1)'>+</button>"
        "<input class='ticket-quantity' type='number' value='0'></td></tr>"
        "</tbody></table></div>"
    )
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert [o.price for o in options] == [500]
    fallback = marks(telemetry, SELECTOR_FALLBACK_MARK)
    assert any(m.detail["field"] == "ticket_unit" and m.detail["rank"] == 2 for m in fallback)


# ------------------------------------------------------------------ 票種選取


async def test_select_tickets_happy_path(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (True, REASON_SELECTED)


async def test_select_tickets_clicks_plus_once_per_ticket(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    await make_adapter(telemetry).select_tickets(page, preference(quantity=3))
    unit = (await page.locator(".ticket-list .ticket-unit").all())[0]
    assert await unit.locator("input.ticket-quantity").first.input_value() == "3"


async def test_select_tickets_accepts_terms(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    await make_adapter(telemetry).select_tickets(page, preference())
    assert page.locator("#person_agree_terms").first.element.get("checked") == "checked"


async def test_select_tickets_records_decision_trace(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    adapter = make_adapter(telemetry)
    await adapter.select_tickets(page, preference())
    decision_marks = marks(telemetry, "ticket_decision")
    assert decision_marks[0].detail["status"] == "SELECTED"
    assert adapter.last_ticket_decision is not None
    assert adapter.last_ticket_decision.trace


async def test_select_tickets_on_sold_out_page(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_sold_out.html")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference(fallback=True))
    assert (ok, reason) == (False, REASON_SOLD_OUT)


async def test_select_tickets_without_units(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div id='registrationsNewApp'></div>")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_NO_TICKET_UNITS)


async def test_select_tickets_detects_quantity_readback_mismatch(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    page.quantity_step = 0
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_QUANTITY_MISMATCH)
    assert marks(telemetry, "quantity_readback_mismatch")


async def test_select_tickets_requires_terms_checkbox(telemetry: TimelineRecorder) -> None:
    page = FakePage(
        "<div id='registrationsNewApp'><div class='ticket-list'>"
        "<div class='ticket-unit' id='ticket_1'><div class='ticket-name'>A</div>"
        "<div class='ticket-price'>NT$ 3,200</div><div class='ticket-status'>剩餘 5 張</div>"
        "<button class='btn-default plus' ng-click='quantityBtnClick(1)'>+</button>"
        "<input class='ticket-quantity' type='number' value='0'></div></div></div>"
    )
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_TERMS_NOT_ACCEPTED)


async def test_select_tickets_reports_missing_plus_button(
    telemetry: TimelineRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    adapter = make_adapter(telemetry)
    original = adapter._locate

    async def only_plus_missing(root: Any, selectors: Any, field: str) -> Any:
        if field == "ticket_plus_btn":
            return None
        return await original(root, selectors, field)

    monkeypatch.setattr(adapter, "_locate", only_plus_missing)
    ok, reason = await adapter.select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_PLUS_BUTTON_MISSING)


# -------------------------------------------------------------------- 座位


async def test_seat_best_available_clicks_auto_assignment(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert await make_adapter(telemetry).handle_seat_selection(page, SeatPreference()) is True
    assert "button[ng-click='challenge(1)']" in page.clicks
    assert marks(telemetry, "seat_action")[0].detail["action"] == "BEST_AVAILABLE"


async def test_seat_specific_zone_downgrades_and_marks(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    preference_ = SeatPreference(strategy="specific_zone", preferred_zones=["A1"])
    assert await make_adapter(telemetry).handle_seat_selection(page, preference_) is True
    assert marks(telemetry, DOWNGRADE_MARK)
    assert marks(telemetry, "seat_action")[0].detail["action"] == "PICK_SEAT"


async def test_seat_falls_back_to_next_step_button(telemetry: TimelineRecorder) -> None:
    page = FakePage(
        "<div id='registrationsNewApp'><div class='register-new-next-button-area'>"
        "<button class='btn btn-primary btn-lg'>下一步</button></div></div>"
    )
    assert await make_adapter(telemetry).handle_seat_selection(page, SeatPreference()) is True


async def test_seat_returns_false_when_no_button_exists(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div id='registrationsNewApp'></div>")
    assert await make_adapter(telemetry).handle_seat_selection(page, SeatPreference()) is False


# -------------------------------------------------------------------- 表單


def contact() -> UserContactProfile:
    return UserContactProfile(name="王小明", phone="0912345678", email="a@b.test")


def attendees(count: int = 2) -> tuple[AttendeeProfile, ...]:
    return tuple(
        AttendeeProfile(name=f"參加人{i}", phone="0987654321", id_number=f"A12345678{i}")
        for i in range(count)
    )


async def test_fill_contact_form_fills_contact_without_submitting(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    adapter = make_adapter(telemetry, attendees=attendees())
    assert await adapter.fill_contact_form(page, contact()) is True
    filled = dict(page.fills)
    assert filled["input[name='contact[name]']"] == "王小明"
    assert filled["input[name='contact[email]']"] == "a@b.test"
    assert page.clicks == []


async def test_submit_order_clicks_confirm(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await make_adapter(telemetry).submit_order(page) is True
    assert "[ng-click='confirmOrder()']" in page.clicks
    assert marks(telemetry, "order_submitted")


async def test_submit_order_reports_missing_button(telemetry: TimelineRecorder) -> None:
    assert await make_adapter(telemetry).submit_order(FakePage("<div></div>")) is False


async def test_fill_contact_form_fills_every_attendee_field(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    adapter = make_adapter(telemetry, attendees=attendees())
    await adapter.fill_contact_form(page, contact())
    filled = dict(page.fills)
    assert filled["input[name='attendees[1][name]']"] == "參加人1"
    assert filled["input[name='attendees[1][id_number]']"] == "A123456781"
    assert marks(telemetry, "attendees_filled")[0].detail["count"] == 2


async def test_fill_contact_form_refuses_to_guess_missing_attendees(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    adapter = make_adapter(telemetry, attendees=attendees(1))
    assert await adapter.fill_contact_form(page, contact()) is False
    detail = marks(telemetry, "attendee_profile_insufficient")[0].detail
    assert detail["required"] == 2
    assert detail["provided"] == 1


async def test_fill_contact_form_reports_missing_contact_field(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div id='orderApp'></div>")
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is False
    assert marks(telemetry, "contact_field_missing")[0].detail["field"] == "contact_name"


async def test_fill_contact_form_without_attendee_fields(telemetry: TimelineRecorder) -> None:
    page = FakePage(
        "<div id='orderApp'><input name='contact[name]'><input name='contact[email]'>"
        "<input name='contact[phone]'><button ng-click='confirmOrder()'>確認</button></div>"
    )
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True
    assert not marks(telemetry, "attendees_filled")
    assert page.clicks == []


# -------------------------------------------------------------------- 驗證


async def test_detect_verification_distinguishes_pages(telemetry: TimelineRecorder) -> None:
    adapter = make_adapter(telemetry)
    order = FakePage.from_fixture("kktix_registration_order.html")
    registration = FakePage.from_fixture("kktix_registration_new.html")
    assert await adapter.detect_verification(order) is True
    assert await adapter.detect_verification(registration) is False


async def test_handle_verification_returns_true_when_absent(telemetry: TimelineRecorder) -> None:
    provider = StubVerification(VerificationResult(True, "42", "stub"))
    adapter = make_adapter(telemetry, verification=provider)
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert await adapter.handle_verification(page) is True
    assert provider.calls == []
    assert marks(telemetry, "verification_absent")


async def test_handle_verification_fills_the_answer(telemetry: TimelineRecorder) -> None:
    provider = StubVerification(VerificationResult(True, "ATA", "stub"))
    adapter = make_adapter(telemetry, verification=provider)
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await adapter.handle_verification(page) is True
    assert "主辦單位" in provider.calls[0].question
    assert dict(page.fills)["input[name='captcha_answer']"] == "ATA"


async def test_handle_verification_without_provider(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await make_adapter(telemetry).handle_verification(page) is False
    assert marks(telemetry, "verification_provider_missing")


async def test_handle_verification_reports_unsolved(telemetry: TimelineRecorder) -> None:
    provider = StubVerification(VerificationResult(False, None, "stub", {"reason": "timeout"}))
    adapter = make_adapter(telemetry, verification=provider)
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await adapter.handle_verification(page) is False
    assert marks(telemetry, "verification_result")[0].detail["solved"] is False


# -------------------------------------------------------------------- 付款


async def test_execute_payment_returns_provider_result_verbatim(telemetry: TimelineRecorder) -> None:
    provider = StubPayment(PaymentOutcome.CHECKPOINT_REACHED)
    adapter = make_adapter(telemetry, payment=provider)
    page = FakePage.from_fixture("kktix_payment.html")
    result = await adapter.execute_payment(page, None)
    assert result.outcome is PaymentOutcome.CHECKPOINT_REACHED
    assert adapter.last_payment_result is result
    assert marks(telemetry, "payment_result")[0].detail["provider"] == "stub_payment"


async def test_execute_payment_flags_real_submission(telemetry: TimelineRecorder) -> None:
    adapter = make_adapter(telemetry, payment=StubPayment(PaymentOutcome.SUBMITTED))
    await adapter.execute_payment(FakePage.from_fixture("kktix_payment.html"), None)
    assert marks(telemetry, "real_payment_submitted")


async def test_execute_payment_guards_cloudflare(telemetry: TimelineRecorder) -> None:
    adapter = make_adapter(telemetry, payment=StubPayment())
    with pytest.raises(CloudflareChallengeError):
        await adapter.execute_payment(FakePage(CLOUDFLARE_HTML), None)
