from __future__ import annotations

from typing import Any

import pytest

from adapters.payment import MockPaymentProvider, PaymentOutcome, PaymentResult
from adapters.ticketing.kktix.adapter import (
    FAILURE_MODAL_MARK,
    FAILURE_MODAL_MISSING_MARK,
    REASON_NO_TICKET_UNITS,
    REASON_NOT_REGISTRATION_PAGE,
    REASON_PLUS_BUTTON_MISSING,
    REASON_QUANTITY_MISMATCH,
    REASON_SELECTED,
    REASON_SOLD_OUT,
    REASON_TERMS_NOT_ACCEPTED,
    RESET_MARK,
    RESET_REASON_MINUS_MISSING,
    RESET_REASON_NO_QUANTITY_FIELD,
    RESET_REASON_NOT_ZERO,
    RESET_REASON_UNREADABLE,
    CloudflareChallengeError,
    KKTIXAdapter,
    KKTIXPageKind,
    PageState,
)
from adapters.ticketing.kktix.dom import (
    DISPATCH_SKIPPED_MARK,
    SELECTOR_FALLBACK_MARK,
    candidate_selectors,
    contains_cloudflare_challenge,
    first_visible,
    ng_click,
    ng_fill,
)
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from adapters.verification.base import (
    VerificationChallenge,
    VerificationResult,
)
from domain.preference import SeatPreference, TicketPreference, TicketPriority
from domain.task import AttendeeProfile, UserContactProfile
from strategy.seat_strategy import DOWNGRADE_MARK
from strategy.ticket_strategy import TicketDecision, TicketOption, decide_ticket
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

    def __init__(
        self, outcome: PaymentOutcome = PaymentOutcome.CHECKPOINT_REACHED
    ) -> None:
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


def preference(
    price: int = 3200, *, quantity: int = 2, fallback: bool = False
) -> TicketPreference:
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


async def test_first_visible_returns_the_first_matching_candidate(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div><button class='plus'>+</button></div>")
    locator = await first_visible(
        page, ["button.plus", "button.minus"], telemetry=telemetry, timeout_ms=20
    )
    assert locator is not None
    assert marks(telemetry, SELECTOR_FALLBACK_MARK) == []


async def test_first_visible_records_rank_when_falling_back(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div><button class='minus'>-</button></div>")
    locator = await first_visible(
        page,
        ["button.plus", "button.minus"],
        telemetry=telemetry,
        timeout_ms=20,
        field="qty",
    )
    assert locator is not None
    fallback = marks(telemetry, SELECTOR_FALLBACK_MARK)
    assert len(fallback) == 1
    assert fallback[0].detail["rank"] == 2
    assert fallback[0].detail["field"] == "qty"


async def test_first_visible_returns_none_instead_of_raising(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div></div>")
    assert (
        await first_visible(page, ["button.plus"], telemetry=telemetry, timeout_ms=20)
        is None
    )


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


async def test_ng_click_survives_the_element_vanishing_on_navigation(
    telemetry: TimelineRecorder,
) -> None:
    """會導航的按鈕一點下去元素就沒了，補送事件必然失敗——那不是錯誤。"""
    page = FakePage("<div><button class='go'>下一步</button></div>")
    page.evaluate_error = RuntimeError("Locator.evaluate: Timeout 1000ms exceeded")
    locator = page.locator("button.go").first
    await ng_click(page, locator, telemetry=telemetry)
    assert page.clicks == ["button.go"]
    assert marks(telemetry, DISPATCH_SKIPPED_MARK)[0].detail["reason"] == "RuntimeError"


async def test_ng_click_dispatch_failure_is_visible_even_without_navigation(
    telemetry: TimelineRecorder,
) -> None:
    """補送失敗一律留痕，不得無聲吞掉。"""
    page = FakePage("<div><input type='checkbox' id='t'></div>")
    page.evaluate_error = RuntimeError("boom")
    await ng_click(page, page.locator("input#t").first, telemetry=telemetry)
    assert len(marks(telemetry, DISPATCH_SKIPPED_MARK)) == 1


async def test_ng_fill_still_propagates_dispatch_failure() -> None:
    """填值不會導航：那裡的補送失敗是真的異常，不得比照點擊放行。"""
    page = FakePage("<div><input id='x'></div>")
    page.evaluate_error = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await ng_fill(page, page.locator("input#x").first, "v")


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


async def test_navigation_does_not_wait_for_the_load_event(
    telemetry: TimelineRecorder,
) -> None:
    """KKTIX 的長尾資源會讓 load 遲遲不觸發；等它就是白等滿逾時後失敗。"""
    page = FakePage.from_fixture("kktix_registration_new.html")
    await make_adapter(telemetry).navigate_to_event(page, "https://reg.test/e/1")
    assert page.goto_kwargs[0]["wait_until"] == "domcontentloaded"


async def test_navigation_timeout_is_separate_from_element_timeout(
    telemetry: TimelineRecorder,
) -> None:
    """開頁比找元素慢得多：兩者共用一個逾時會讓真站導航必然失敗。"""
    page = FakePage.from_fixture("kktix_registration_new.html")
    adapter = make_adapter(telemetry)
    await adapter.navigate_to_event(page, "https://reg.test/e/1")
    assert adapter.timeout_ms == 20
    assert page.goto_kwargs[0]["timeout"] == adapter.navigation_timeout_ms == 30000


async def test_navigate_to_event_reuses_the_page_it_is_already_on(
    telemetry: TimelineRecorder,
) -> None:
    """CDP 借來的分頁已經停在目標活動上：重新 goto 只會敲掉人工通過的狀態。"""
    url = "https://reg.test/events/demo/registrations/new"
    page = FakePage.from_fixture("kktix_registration_new.html", url=url)
    adapter = make_adapter(telemetry)
    assert await adapter.navigate_to_event(page, url) is True
    assert page.goto_urls == []
    assert marks(telemetry, "navigated")[0].detail["reused"] is True


async def test_navigate_to_event_reuses_registration_page_for_event_target(
    telemetry: TimelineRecorder,
) -> None:
    """同一場活動的登記頁比活動頁更深，導回活動頁是倒退。"""
    page = FakePage.from_fixture(
        "kktix_registration_new.html",
        url="https://org.kktix.test/events/demo/registrations/new",
    )
    adapter = make_adapter(telemetry)
    await adapter.navigate_to_event(page, "https://reg.test/events/demo")
    assert page.goto_urls == []


async def test_navigate_to_event_still_goes_when_only_on_the_event_page(
    telemetry: TimelineRecorder,
) -> None:
    """反向不成立：停在活動頁而目標是登記頁時，還沒走到下得了單的那一頁。"""
    target = "https://reg.test/events/demo/registrations/new"
    page = FakePage.from_fixture(
        "kktix_registration_new.html", url="https://org.kktix.test/events/demo"
    )
    await make_adapter(telemetry).navigate_to_event(page, target)
    assert page.goto_urls == [target]


async def test_navigate_to_event_goes_when_the_event_differs(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture(
        "kktix_registration_new.html", url="https://reg.test/events/demo"
    )
    await make_adapter(telemetry).navigate_to_event(
        page, "https://reg.test/events/other"
    )
    assert page.goto_urls == ["https://kktix.com/events/other/registrations/new"]


async def test_navigate_to_event_upgrades_an_event_page_url_to_the_registration_page(
    telemetry: TimelineRecorder,
) -> None:
    """活動主頁網址下不了單；不轉成登記頁就會停在永遠不會就緒的一頁上。"""
    page = FakePage.from_fixture("kktix_event_page.html", url="about:blank")
    await make_adapter(telemetry).navigate_to_event(
        page, "https://atc-twn.kktix.cc/events/d8a6cdd1"
    )
    # 第一次導航必須是登記頁；單場次活動找不到場次清單，之後的回頭探查不下單。
    assert page.goto_urls[0] == "https://kktix.com/events/d8a6cdd1/registrations/new"


async def test_multi_session_event_enters_the_chosen_session(
    telemetry: TimelineRecorder,
) -> None:
    """多場次活動的母登記頁沒有票種；必須依偏好選進該場次自己的登記頁。"""
    sessions = [
        {"url": "https://ticketing.example.test/events/morning/registrations/new", "label": "【上午場】 2026/10/03 13:00"},
        {"url": "https://ticketing.example.test/events/evening/registrations/new", "label": "【下午場】 2026/10/03 18:00"},
    ]
    adapter = make_adapter(telemetry)
    assert adapter.pick_session(sessions, "上午場") == sessions[0]["url"]
    assert adapter.pick_session(sessions, "下午場") == sessions[1]["url"]


async def test_single_session_event_is_not_counted_as_many(
    telemetry: TimelineRecorder,
) -> None:
    """單場次活動的「立即購票」「下一步」會有多顆按鈕指向同一個登記頁。

    不以網址去重的話，一場會被數成三場，於是挑不出唯一解而整個卡住。
    """
    same = "https://ticketing.example.test/events/single/registrations/new"
    sessions = [
        {"url": same, "label": "立即購票"},
        {"url": same, "label": "立即購票"},
        {"url": same, "label": "下一步"},
    ]
    adapter = make_adapter(telemetry)
    assert adapter.pick_session(sessions, None) == same
    # 單場次時偏好沒有意義，填了也不該把它擋掉。
    assert adapter.pick_session(sessions, "上午場") == same


async def test_multi_session_event_never_guesses_the_session(
    telemetry: TimelineRecorder,
) -> None:
    """買錯場次與買不到一樣糟，而且不可逆——挑不出唯一一個就不要挑。"""
    sessions = [
        {"url": "https://ticketing.example.test/events/a/registrations/new", "label": "【上午場】"},
        {"url": "https://ticketing.example.test/events/b/registrations/new", "label": "【下午場】"},
    ]
    adapter = make_adapter(telemetry)
    assert adapter.pick_session(sessions, None) is None
    assert adapter.pick_session(sessions, "晚上場") is None
    # 單場次活動不需要偏好也能決定。
    assert adapter.pick_session(sessions[:1], None) == sessions[0]["url"]


async def test_probe_page_upgrades_an_event_page_url_to_the_registration_page(
    telemetry: TimelineRecorder,
) -> None:
    """就緒閘門只認登記頁；停在主頁上必須再走一步，不能当成「已在目標頁」。"""
    page = FakePage.from_fixture(
        "kktix_registration_new.html", url="https://atc-twn.kktix.cc/events/d8a6cdd1"
    )
    kind = await make_adapter(telemetry).probe_page(
        page, "https://atc-twn.kktix.cc/events/d8a6cdd1"
    )
    assert page.goto_urls == ["https://kktix.com/events/d8a6cdd1/registrations/new"]
    assert kind is KKTIXPageKind.REGISTRATION


async def test_probe_page_skips_navigation_when_already_on_target(
    telemetry: TimelineRecorder,
) -> None:
    """就地判讀與重整後判讀讀到同一件事，重整卻會敲掉現場狀態。"""
    url = "https://reg.test/events/demo/registrations/new"
    page = FakePage.from_fixture("kktix_registration_new.html", url=url)
    kind = await make_adapter(telemetry).probe_page(page, url)
    assert kind is KKTIXPageKind.REGISTRATION
    assert page.goto_urls == []


async def test_probe_page_navigates_when_elsewhere(
    telemetry: TimelineRecorder,
) -> None:
    target = "https://reg.test/events/demo/registrations/new"
    page = FakePage.from_fixture(
        "kktix_registration_new.html", url="https://reg.test/users/sign_in"
    )
    await make_adapter(telemetry).probe_page(page, target)
    assert page.goto_urls == [target]


async def test_cloudflare_challenge_aborts_and_screenshots(
    telemetry: TimelineRecorder,
) -> None:
    shots: list[str] = []

    async def screenshot(name: str) -> None:
        shots.append(name)

    adapter = make_adapter(telemetry, screenshot=screenshot)
    with pytest.raises(CloudflareChallengeError):
        await adapter.navigate_to_event(
            FakePage(CLOUDFLARE_HTML), "https://reg.test/e/1"
        )
    assert shots == ["cloudflare_challenge_navigate"]
    assert marks(telemetry, "cloudflare_challenge")


async def test_cloudflare_guard_also_applies_to_ticket_selection(
    telemetry: TimelineRecorder,
) -> None:
    adapter = make_adapter(telemetry)
    with pytest.raises(CloudflareChallengeError):
        await adapter.select_tickets(FakePage(CLOUDFLARE_HTML), preference())


async def test_detect_sale_opened_true_on_registration_page(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert await make_adapter(telemetry).detect_sale_opened(page, 20) is True


async def test_detect_sale_opened_false_without_registration_app(
    telemetry: TimelineRecorder,
) -> None:
    assert (
        await make_adapter(telemetry).detect_sale_opened(FakePage("<div></div>"), 20)
        is False
    )


# ------------------------------------------------------------------ 頁面判別


async def test_detect_page_kind_registration(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert (
        await make_adapter(telemetry).detect_page_kind(page)
        is KKTIXPageKind.REGISTRATION
    )


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


async def test_event_page_tickets_are_read_from_the_table(
    telemetry: TimelineRecorder,
) -> None:
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
    assert (
        marks(telemetry, "wrong_page_for_selection")[0].detail["page_kind"] == "EVENT"
    )


async def test_selecting_on_a_login_redirect_is_not_reported_as_sold_out(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div>請先登入 KKTIX 帳號</div>")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_NOT_REGISTRATION_PAGE)
    assert (
        marks(telemetry, "wrong_page_for_selection")[0].detail["page_kind"] == "UNKNOWN"
    )


# ------------------------------------------------------------------ 票種讀取


async def test_read_ticket_options_parses_snapshot(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert [o.name for o in options] == ["全票 A 區", "全票 B 區", "搖滾站區"]
    assert [o.price for o in options] == [3200, 2400, 4800]
    assert [o.remaining for o in options] == [12, 3, None]


async def test_read_ticket_options_marks_sold_out_unit_unavailable(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    options = await make_adapter(telemetry).read_ticket_options(page)
    assert [o.available for o in options] == [True, True, False]
    assert options[2].status_text == "已售完"


async def test_ticket_unit_selector_fallback_is_recorded(
    telemetry: TimelineRecorder,
) -> None:
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
    assert any(
        m.detail["field"] == "ticket_unit" and m.detail["rank"] == 2 for m in fallback
    )


# ------------------------------------------------------------------ 票種選取


async def test_select_tickets_happy_path(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (True, REASON_SELECTED)


# 實站實測形狀（2026-09 kktix.com 登記頁）：數量欄位只有 ng-model，
# 既無 `ticket-quantity` class 也不是 `type=number`。
LIVE_SHAPE_HTML = (
    "<div id='registrationsNewApp'><div class='ticket-list'>"
    "<div class='ticket-unit'><div class='ticket-name'>全票</div>"
    "<div class='ticket-price'>NT$ 3,200</div>"
    "<button class='btn-default plus' ng-click='quantityBtnClick(1)'></button>"
    "<input type='text' ng-model='ticketModel.quantity' value='0'>"
    "</div></div>"
    "<label>我已經閱讀並同意"
    "<input type='checkbox' id='person_agree_terms' ng-model='conditions.agreeTerm'>"
    "</label></div>"
)


async def test_select_tickets_handles_the_live_ng_model_quantity_field(
    telemetry: TimelineRecorder,
) -> None:
    """舊候選全部落空的實站形狀：回讀必須成功，不得滑成 QUANTITY_MISMATCH。"""
    page = FakePage(LIVE_SHAPE_HTML)
    ok, reason = await make_adapter(telemetry).select_tickets(
        page, preference(price=3200, quantity=1)
    )
    assert (ok, reason) == (True, REASON_SELECTED)
    unit = (await page.locator(".ticket-list .ticket-unit").all())[0]
    quantity = unit.locator("input[ng-model='ticketModel.quantity']").first
    assert await quantity.input_value() == "1"
    assert page.locator("#person_agree_terms").first.element.get("checked") == "checked"


def test_quantity_selector_prefers_the_live_ng_model_candidate() -> None:
    """順位即契約：實站形狀必須是第一順位，舊模板降為 fallback。"""
    from adapters.ticketing.kktix.selectors import KKTIXSelectors

    candidates = candidate_selectors(KKTIXSelectors.TICKET_QUANTITY_INPUT)
    assert candidates[0] == "input[ng-model='ticketModel.quantity']"
    assert "input.ticket-quantity" in candidates
    assert "input[type='number']" in candidates


async def test_select_tickets_clicks_plus_once_per_ticket(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    await make_adapter(telemetry).select_tickets(page, preference(quantity=3))
    unit = (await page.locator(".ticket-list .ticket-unit").all())[0]
    assert await unit.locator("input.ticket-quantity").first.input_value() == "3"


async def test_select_tickets_accepts_terms(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    await make_adapter(telemetry).select_tickets(page, preference())
    assert page.locator("#person_agree_terms").first.element.get("checked") == "checked"


async def test_select_tickets_records_decision_trace(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    adapter = make_adapter(telemetry)
    await adapter.select_tickets(page, preference())
    decision_marks = marks(telemetry, "ticket_decision")
    assert decision_marks[0].detail["status"] == "SELECTED"
    assert adapter.last_ticket_decision is not None
    assert adapter.last_ticket_decision.trace


async def test_select_tickets_on_sold_out_page(telemetry: TimelineRecorder) -> None:
    page = FakePage.from_fixture("kktix_sold_out.html")
    ok, reason = await make_adapter(telemetry).select_tickets(
        page, preference(fallback=True)
    )
    assert (ok, reason) == (False, REASON_SOLD_OUT)


async def test_select_tickets_without_units(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div id='registrationsNewApp'></div>")
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_NO_TICKET_UNITS)


async def test_select_tickets_detects_quantity_readback_mismatch(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    page.quantity_step = 0
    ok, reason = await make_adapter(telemetry).select_tickets(page, preference())
    assert (ok, reason) == (False, REASON_QUANTITY_MISMATCH)
    assert marks(telemetry, "quantity_readback_mismatch")


async def test_select_tickets_requires_terms_checkbox(
    telemetry: TimelineRecorder,
) -> None:
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


async def test_seat_best_available_clicks_auto_assignment(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, SeatPreference())
        is True
    )
    assert "button[ng-click='challenge(1)']" in page.clicks
    assert marks(telemetry, "seat_action")[0].detail["action"] == "BEST_AVAILABLE"


async def test_seat_specific_zone_downgrades_and_marks(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_new.html")
    preference_ = SeatPreference(strategy="specific_zone", preferred_zones=["A1"])
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, preference_) is True
    )
    assert marks(telemetry, DOWNGRADE_MARK)
    assert marks(telemetry, "seat_action")[0].detail["action"] == "PICK_SEAT"


async def test_seat_falls_back_to_next_step_button(telemetry: TimelineRecorder) -> None:
    page = FakePage(
        "<div id='registrationsNewApp'><div class='register-new-next-button-area'>"
        "<button class='btn btn-primary btn-lg'>下一步</button></div></div>"
    )
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, SeatPreference())
        is True
    )


# 票種選擇頁兩種形狀：不劃位只有「下一步」，劃位則「自行選位／電腦配位」並存。
NEXT_STEP_ONLY_HTML = (
    "<div id='registrationsNewApp'><div class='register-new-next-button-area'>"
    "<button class='btn btn-primary btn-lg' ng-click='challenge()'>下一步</button>"
    "</div></div>"
)
RESERVED_SEATING_HTML = (
    "<div id='registrationsNewApp'><div class='register-new-next-button-area'>"
    "<button class='btn btn-primary' ng-click='challenge()'>自行選位</button>"
    "<button class='btn btn-primary' ng-click='challenge(1)'>電腦配位</button>"
    "</div></div>"
)


async def test_seat_next_step_only_page_is_not_reported_as_pick_seat(
    telemetry: TimelineRecorder,
) -> None:
    """不劃位頁的「下一步」也是 `challenge()`——不得因此被当成自行選位回報。"""
    page = FakePage(NEXT_STEP_ONLY_HTML)
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, SeatPreference())
        is True
    )
    detail = marks(telemetry, "seat_action")[0].detail
    assert detail["page_shape"] == "single_next_step"
    assert detail["action"] == "NEXT_STEP"


async def test_seat_reserved_page_prefers_auto_assignment_over_leftmost_button(
    telemetry: TimelineRecorder,
) -> None:
    """劃位頁：自行選位在左、電腦配位在右，點到的必須是電腦配位。"""
    page = FakePage(RESERVED_SEATING_HTML)
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, SeatPreference())
        is True
    )
    assert page.clicks == ["button[ng-click='challenge(1)']"]
    detail = marks(telemetry, "seat_action")[0].detail
    assert detail["page_shape"] == "reserved_seating"
    assert detail["action"] == "BEST_AVAILABLE"


async def test_seat_reserved_page_honours_explicit_pick_seat(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(RESERVED_SEATING_HTML)
    preference_ = SeatPreference(strategy="same_zone")
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, preference_) is True
    )
    assert page.clicks == ["button[ng-click='challenge()']"]
    detail = marks(telemetry, "seat_action")[0].detail
    assert (detail["action"], detail["requested"]) == ("PICK_SEAT", "PICK_SEAT")


async def test_seat_returns_false_when_no_button_exists(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div id='registrationsNewApp'></div>")
    assert (
        await make_adapter(telemetry).handle_seat_selection(page, SeatPreference())
        is False
    )


# -------------------------------------------------------------------- 表單


def contact() -> UserContactProfile:
    return UserContactProfile(name="王小明", phone="0912345678", email="a@b.test")


def attendees(count: int = 2) -> tuple[AttendeeProfile, ...]:
    return tuple(
        AttendeeProfile(
            name=f"參加人{i}", phone="0987654321", id_number=f"A12345678{i}"
        )
        for i in range(count)
    )


async def test_fill_contact_form_fills_contact_without_submitting(
    telemetry: TimelineRecorder,
) -> None:
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


async def test_fill_contact_form_fills_every_attendee_field(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    adapter = make_adapter(telemetry, attendees=attendees())
    await adapter.fill_contact_form(page, contact())
    filled = dict(page.fills)
    assert filled["input[name='attendees[1][name]']"] == "參加人1"
    assert filled["input[name='attendees[1][id_number]']"] == "A123456781"
    assert marks(telemetry, "attendees_filled")[0].detail["count"] == 2


async def test_fill_contact_form_refuses_to_guess_missing_attendees(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    adapter = make_adapter(telemetry, attendees=attendees(1))
    assert await adapter.fill_contact_form(page, contact()) is False
    detail = marks(telemetry, "attendee_profile_insufficient")[0].detail
    assert detail["required"] == 2
    assert detail["provided"] == 1


def _dynamic_contact_html(*, name: str = "", email: str = "", phone: str = "") -> str:
    """實站形狀（2026-09 kktix.com 訂單頁）：name 帶活動專屬數字 ID，
    三個欄位共用同一個 ng-model，只有 label 文字能區分。"""

    def group(kind: str, label: str, field: str, value: str) -> str:
        return (
            f"<div class='control-group {kind}'>"
            f"<label class='control-label ng-binding'>* {label}</label>"
            "<div class='controls form-inline ng-scope'><div class='ng-scope'>"
            f"<input type='text' name='contact[{field}]' "
            f"ng-model='contactModel[field.field_key]' value='{value}'>"
            "</div></div></div>"
        )

    return (
        "<div id='orderApp'><div class='contact-info'>"
        + group("text", "姓名", "field_text_1007673", name)
        + group("email", "Email", "field_email_1007674", email)
        + group("text", "手機", "field_text_1007675", phone)
        + "</div><button ng-click='confirmOrder()'>確認</button></div>"
    )


async def test_fill_contact_form_resolves_dynamically_named_fields(
    telemetry: TimelineRecorder,
) -> None:
    """静態候選全數落空的實站表單：靠 label 文字歸位後必須填得進去。"""
    page = FakePage(_dynamic_contact_html())
    adapter = make_adapter(telemetry)
    assert await adapter.fill_contact_form(page, contact()) is True
    values = {
        i.element.get("name"): i.element.get("value")
        for i in await page.locator("input[name^='contact[']").all()
    }
    assert values == {
        "contact[field_text_1007673]": "王小明",
        "contact[field_email_1007674]": "a@b.test",
        "contact[field_text_1007675]": "0912345678",
    }
    assert marks(telemetry, "contact_fields_resolved_dynamically")[0].detail[
        "fields"
    ] == ["contact_email", "contact_name", "contact_phone"]


async def test_fill_contact_form_waits_for_the_async_rendered_order_form(
    telemetry: TimelineRecorder,
) -> None:
    """點完配位會導頁，表單是非同步渲染：不等它就列舉欄位永遠拿到空集合。"""
    page = FakePage("<div id='orderApp'></div>")
    page.render_after_wait(_dynamic_contact_html())
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True
    assert marks(telemetry, "contact_fields_resolved_dynamically")
    assert not marks(telemetry, "contact_field_missing")


async def test_fill_contact_form_separates_unrendered_form_from_missing_field(
    telemetry: TimelineRecorder,
) -> None:
    """表單根本沒渲染出來要能跟「欄位不存在」區分，否則追因會走錯方向。"""
    page = FakePage("<div id='orderApp'></div>")
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is False
    assert marks(telemetry, "contact_form_not_rendered")
    assert (
        marks(telemetry, "contact_field_missing")[0].detail["field"] == "contact_name"
    )


async def test_fill_contact_form_never_overwrites_account_prefilled_values(
    telemetry: TimelineRecorder,
) -> None:
    """KKTIX 用登入帳號預填真實資料時，不得拿任務檔的佔位資料覆寫。"""
    page = FakePage(
        _dynamic_contact_html(
            name="曹宇鑑", email="real@example.com", phone="886965303635"
        )
    )
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True
    assert page.fills == []
    values = {
        i.element.get("name"): i.element.get("value")
        for i in await page.locator("input[name^='contact[']").all()
    }
    assert values["contact[field_text_1007673]"] == "曹宇鑑"
    assert values["contact[field_email_1007674]"] == "real@example.com"
    prefilled = marks(telemetry, "contact_field_prefilled")
    assert {m.detail["field"] for m in prefilled} == {
        "contact_name",
        "contact_email",
        "contact_phone",
    }
    assert all(m.detail["matches_profile"] is False for m in prefilled)


# 選配探測的預算契約：以「不存在」為常態的檢查不得燒掉必要元素的長預算。


async def test_detect_verification_probes_with_the_short_budget(
    telemetry: TimelineRecorder,
) -> None:
    """沒驗證題是常態：不得為了確認「真的沒有」而等整份 timeout。"""
    page = FakePage("<div id='orderApp'><p>no captcha here</p></div>")
    # 必須用正式預設的元素預算：測試幫手的 timeout_ms 小到會被 min() 夾成與
    # 探測預算同值，那樣這條斷言就算改回長預算也不會紅，形同虛設。
    adapter = KKTIXAdapter(
        telemetry=telemetry, payment=MockPaymentProvider(), timeout_ms=5000
    )
    assert adapter.probe_timeout_ms < adapter.timeout_ms
    assert await adapter.detect_verification(page) is False
    assert marks(telemetry, "verification_probe")[0].detail["present"] is False
    waited = [t for sel, t in page.wait_timeouts if t is not None]
    assert waited, "探測未發生，預算契約無法驗証"
    assert sum(waited) <= adapter.probe_timeout_ms


async def test_mock_payment_probes_absent_card_fields_with_the_short_budget() -> None:
    """ATM 等非刷卡頁根本沒有卡片欄位，dry-run 不应為此白燒三份預算。"""
    page = FakePage("<div id='paymentApp'><p>ATM 轉帳</p></div>")
    provider = MockPaymentProvider(timeout_ms=2000, probe_timeout_ms=500)
    result = await provider.pay(page, None)
    assert result.detail["submitted"] is False
    # 依証冊表列出卡片欄位的候選，避免用字串類似誤捕到付款 radio（它是錨點，刷長預算）。
    card_selectors = {
        candidate
        for group in (
            KKTIXSelectors.CARD_NUMBER_INPUT,
            KKTIXSelectors.CARD_EXPIRY_INPUT,
            KKTIXSelectors.CARD_CVV_INPUT,
        )
        for candidate in candidate_selectors(group)
    }
    card_waits = [
        t for sel, t in page.wait_timeouts if t is not None and sel in card_selectors
    ]
    assert card_waits, "卡片欄位未被探測"
    assert sum(card_waits) <= provider.probe_timeout_ms * 3
    assert sum(card_waits) < provider.timeout_ms * 3


def test_probe_budget_never_exceeds_the_element_budget(
    telemetry: TimelineRecorder,
) -> None:
    """呼叫端把 timeout_ms 調小時，探測預算必須跟著縮，不得反過來成為矶頸。"""

    def adapter_with(**kwargs: Any) -> KKTIXAdapter:
        return KKTIXAdapter(
            telemetry=telemetry, payment=MockPaymentProvider(), **kwargs
        )

    assert adapter_with(timeout_ms=5000).probe_timeout_ms == 500
    assert adapter_with(timeout_ms=20).probe_timeout_ms == 20
    assert adapter_with(timeout_ms=5000, probe_timeout_ms=120).probe_timeout_ms == 120
    assert MockPaymentProvider(timeout_ms=2000).probe_timeout_ms == 500
    assert MockPaymentProvider(timeout_ms=100).probe_timeout_ms == 100


CONSENT_HTML = (
    "<div class='control-group checkbox'>"
    "<label class='control-label ng-binding'>* 同意條款</label>"
    "<div class='controls form-inline ng-scope' ng-switch='field.field_type'>"
    "<div ng-switch-when='checkbox' class='ng-scope'>"
    "<label class='checkbox ng-binding ng-scope'>"
    "<input type='checkbox' ng-model='contactModel[field.field_key][option.id]' "
    "name='contact[field_checkbox_1010782]' value=''>"
    "我同意KKTIX系統所分配之門票，購買後將不能更改或退款。"
    "</label></div></div></div>"
)


async def test_fill_contact_form_accepts_dynamic_consent_checkbox(
    telemetry: TimelineRecorder,
) -> None:
    """動態同意條款 checkbox 沒勾就送不出去，且勾了什麼必須留下條款文字。"""
    page = FakePage(
        _dynamic_contact_html().replace("</div><button", CONSENT_HTML + "</div><button")
    )
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True
    box = page.locator("input[name='contact[field_checkbox_1010782]']").first
    assert await box.is_checked(), "同意條款未被勾選"
    accepted = marks(telemetry, "contact_consent_accepted")[0].detail
    assert accepted["count"] == 1
    assert "不能更改或退款" in accepted["terms"][0]


async def test_fill_contact_form_does_not_fill_text_into_a_checkbox(
    telemetry: TimelineRecorder,
) -> None:
    """欄位標籤是主辦自己打的自由文字，checkbox 的標籤完全可能含「姓名」。

    它同樣是 contact[...] 開頭，一旦被當成文字欄位就會被 ng_fill 填進字串。
    """
    # 刻意排在真正的姓名欄位之前：它不得偷走 contact_name 那個位子。
    checkbox_group = (
        "<div class='control-group checkbox'>"
        "<label class='control-label ng-binding'>* 姓名公開</label>"
        "<div class='controls form-inline ng-scope'>"
        "<label class='checkbox ng-binding'>"
        "<input type='checkbox' name='contact[field_checkbox_2001]' value=''>同意公開"
        "</label></div></div>"
    )
    page = FakePage(
        _dynamic_contact_html().replace(
            "<div class='contact-info'>", "<div class='contact-info'>" + checkbox_group
        )
    )
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True
    box = page.locator("input[name='contact[field_checkbox_2001]']").first
    assert box.element.get("value") == "", "checkbox 被填了字串"
    assert await box.is_checked(), "同意欄位應該被勾選而不是被填字"
    values = {
        i.element.get("name"): i.element.get("value")
        for i in await page.locator("input[name^='contact[']").all()
    }
    assert values["contact[field_text_1007673]"] == "王小明"


async def test_seat_shape_detection_uses_the_short_probe_budget(
    telemetry: TimelineRecorder,
) -> None:
    """「電腦配位不存在」就是不劃位頁的辨識依據：不得為這個常態等整份預算。"""
    page = FakePage(NEXT_STEP_ONLY_HTML)
    adapter = KKTIXAdapter(
        telemetry=telemetry, payment=MockPaymentProvider(), timeout_ms=5000
    )
    assert await adapter.handle_seat_selection(page, SeatPreference()) is True
    best_available = set(candidate_selectors(KKTIXSelectors.BTN_BEST_AVAILABLE))
    waited = [
        t for sel, t in page.wait_timeouts if t is not None and sel in best_available
    ]
    assert waited, "未探測電腦配位，預算契約無法驗証"
    assert sum(waited) <= adapter.probe_timeout_ms


async def test_page_text_retries_once_while_the_page_is_navigating(
    telemetry: TimelineRecorder,
) -> None:
    """點完會導頁的按鈕後立刻讀 content() 會拋錯，那是問得太早而不是壞頁面。"""
    from adapters.ticketing.kktix.dom import page_text

    page = FakePage(_dynamic_contact_html())
    page.content_error_once = RuntimeError(
        "Page.content: Unable to retrieve content because the page is navigating"
    )
    text = await page_text(page)
    assert "contact[field_text_1007673]" in text
    assert page.load_states == ["domcontentloaded"]


async def test_fill_contact_form_survives_a_navigating_page(
    telemetry: TimelineRecorder,
) -> None:
    """導頁中的 Cloudflare 閃存檢查不得把整條流程弄死。"""
    page = FakePage(_dynamic_contact_html())
    page.content_error_once = RuntimeError(
        "Page.content: Unable to retrieve content because the page is navigating"
    )
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True


async def test_fill_contact_form_reports_missing_contact_field(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div id='orderApp'></div>")
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is False
    assert (
        marks(telemetry, "contact_field_missing")[0].detail["field"] == "contact_name"
    )


async def test_fill_contact_form_without_attendee_fields(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(
        "<div id='orderApp'><input name='contact[name]'><input name='contact[email]'>"
        "<input name='contact[phone]'><button ng-click='confirmOrder()'>確認</button></div>"
    )
    assert await make_adapter(telemetry).fill_contact_form(page, contact()) is True
    assert not marks(telemetry, "attendees_filled")
    assert page.clicks == []


# -------------------------------------------------------------------- 驗證


async def test_detect_verification_distinguishes_pages(
    telemetry: TimelineRecorder,
) -> None:
    adapter = make_adapter(telemetry)
    order = FakePage.from_fixture("kktix_registration_order.html")
    registration = FakePage.from_fixture("kktix_registration_new.html")
    assert await adapter.detect_verification(order) is True
    assert await adapter.detect_verification(registration) is False


async def test_handle_verification_returns_true_when_absent(
    telemetry: TimelineRecorder,
) -> None:
    provider = StubVerification(VerificationResult(True, "42", "stub"))
    adapter = make_adapter(telemetry, verification=provider)
    page = FakePage.from_fixture("kktix_registration_new.html")
    assert await adapter.handle_verification(page) is True
    assert provider.calls == []
    assert marks(telemetry, "verification_absent")


async def test_handle_verification_fills_the_answer(
    telemetry: TimelineRecorder,
) -> None:
    provider = StubVerification(VerificationResult(True, "ATA", "stub"))
    adapter = make_adapter(telemetry, verification=provider)
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await adapter.handle_verification(page) is True
    assert "主辦單位" in provider.calls[0].question
    assert dict(page.fills)["input[name='captcha_answer']"] == "ATA"


async def test_handle_verification_without_provider(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await make_adapter(telemetry).handle_verification(page) is False
    assert marks(telemetry, "verification_provider_missing")


async def test_handle_verification_reports_unsolved(
    telemetry: TimelineRecorder,
) -> None:
    provider = StubVerification(
        VerificationResult(False, None, "stub", {"reason": "timeout"})
    )
    adapter = make_adapter(telemetry, verification=provider)
    page = FakePage.from_fixture("kktix_registration_order.html")
    assert await adapter.handle_verification(page) is False
    assert marks(telemetry, "verification_result")[0].detail["solved"] is False


# -------------------------------------------------------------------- 付款


async def test_execute_payment_returns_provider_result_verbatim(
    telemetry: TimelineRecorder,
) -> None:
    provider = StubPayment(PaymentOutcome.CHECKPOINT_REACHED)
    adapter = make_adapter(telemetry, payment=provider)
    page = FakePage.from_fixture("kktix_payment.html")
    result = await adapter.execute_payment(page, None)
    assert result.outcome is PaymentOutcome.CHECKPOINT_REACHED
    assert adapter.last_payment_result is result
    assert marks(telemetry, "payment_result")[0].detail["provider"] == "stub_payment"


async def test_execute_payment_flags_real_submission(
    telemetry: TimelineRecorder,
) -> None:
    adapter = make_adapter(telemetry, payment=StubPayment(PaymentOutcome.SUBMITTED))
    await adapter.execute_payment(FakePage.from_fixture("kktix_payment.html"), None)
    assert marks(telemetry, "real_payment_submitted")


async def test_execute_payment_guards_cloudflare(telemetry: TimelineRecorder) -> None:
    adapter = make_adapter(telemetry, payment=StubPayment())
    with pytest.raises(CloudflareChallengeError):
        await adapter.execute_payment(FakePage(CLOUDFLARE_HTML), None)


# ------------------------------------------------------- 頁面狀態偵測（PageState）

# 新頁型一律以本檔內的 inline 常數呈現：`tests/fixtures/*` 是凍結的實站快照，
# 為了測試而改動它們等於偽造原始觀測資料。

TICKET_SELECTION_HTML = (
    "<div id='registrationsNewApp'><div class='ticket-list'>"
    "<div class='ticket-unit'><div class='ticket-name'>全票</div>"
    "<div class='ticket-price'>NT$ 3,200</div>"
    "<button class='btn-default plus' ng-click='quantityBtnClick(1)'></button>"
    "<button class='btn-default minus' ng-click='quantityBtnClick(-1)'></button>"
    "<input type='text' ng-model='ticketModel.quantity' value='0'>"
    "</div></div>"
    "<label>我已經閱讀並同意"
    "<input type='checkbox' id='person_agree_terms' ng-model='conditions.agreeTerm'>"
    "</label></div>"
)
SEAT_SELECTION_HTML = TICKET_SELECTION_HTML.replace(
    "ticketModel.quantity' value='0'", "ticketModel.quantity' value='2'"
).replace(
    "</div></div>",
    "</div></div><button ng-click='challenge(1)'>電腦配位</button>",
    1,
)
FAILURE_MODAL_HTML = (
    "<div class='modal in'><div class='modal-body'>別人搶先一步</div>"
    "<button class='close'>×</button></div>"
)
GUEST_MODAL_HTML = (
    "<div class='modal in'><div class='modal-body'>立刻成為 KKTIX 會員</div>"
    "<button class='close'>×</button></div>"
)
QUEUE_HTML = "<div id='cf-wrapper'><h1>正在排隊</h1><span id='cf-time'>03:21</span></div>"
QUALIFICATION_CODE_HTML = (
    "<div id='registrationsNewApp'><div class='code-input'>"
    "<input type='text' ng-model='code'>"
    "<button ng-click='verifyCode()'>驗證</button></div></div>"
)
FORM_FILLING_HTML = (
    "<div class='contact-info'><div class='control-group'>"
    "<label class='control-label'>姓名</label>"
    "<input name='contact[name]' value=''></div></div>"
)
STANDALONE_CAPTCHA_HTML = (
    "<div class='custom-captcha-inner'><p>主辦單位的英文縮寫？</p>"
    "<input name='captcha_answer' value=''></div>"
)


async def detect(telemetry: TimelineRecorder, page: FakePage) -> PageState:
    return await make_adapter(telemetry).detect_page_state(page)


async def test_detect_page_state_failure_modal_outranks_the_page_beneath(
    telemetry: TimelineRecorder,
) -> None:
    """彈窗蓋住底下的登記頁；先處理彈窗才不會對著被擋住的 DOM 空點。"""
    page = FakePage(TICKET_SELECTION_HTML + FAILURE_MODAL_HTML)
    assert await detect(telemetry, page) is PageState.FAILURE_MODAL


async def test_detect_page_state_ignores_the_hidden_modal_template(
    telemetry: TimelineRecorder,
) -> None:
    """Bootstrap 把彈窗骨架留在 DOM 裡：只看存在與否會把整場搶票誤判成搶輸。"""
    hidden = FAILURE_MODAL_HTML.replace(
        "class='modal in'", "class='modal in' style='display: none'"
    )
    page = FakePage(TICKET_SELECTION_HTML + hidden)
    assert await detect(telemetry, page) is PageState.TICKET_SELECTION


async def test_detect_page_state_guest_modal(telemetry: TimelineRecorder) -> None:
    page = FakePage(TICKET_SELECTION_HTML + GUEST_MODAL_HTML)
    assert await detect(telemetry, page) is PageState.GUEST_MODAL


async def test_detect_page_state_queue_is_not_mistaken_for_the_registration_page(
    telemetry: TimelineRecorder,
) -> None:
    """排隊室可能與登記頁共存於同一個網址；判成選票就會對著等候室亂點。"""
    page = FakePage(QUEUE_HTML + TICKET_SELECTION_HTML)
    assert await detect(telemetry, page) is PageState.QUEUE


async def test_detect_page_state_completed_from_order_url(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div>訂單成立</div>", url="https://reg.test/orders/8871")
    assert await detect(telemetry, page) is PageState.COMPLETED


async def test_detect_page_state_completed_from_dom_container(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div id='orderShowApp'>保留訂單</div>")
    assert await detect(telemetry, page) is PageState.COMPLETED


async def test_detect_page_state_payment_url_is_not_reported_as_completed(
    telemetry: TimelineRecorder,
) -> None:
    """付款頁網址同樣帶 `/orders/`；判成完成就會把未付款訂單寫成已抵達終點。"""
    page = FakePage("<div>請選擇付款方式</div>", url="https://reg.test/orders/8/payment")
    assert await detect(telemetry, page) is PageState.PAYMENT_REQUIRED


async def test_detect_page_state_payment_from_dom_radio(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<input type='radio' value='credit_card_x'>")
    assert await detect(telemetry, page) is PageState.PAYMENT_REQUIRED


async def test_detect_page_state_qualification_code(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(QUALIFICATION_CODE_HTML)
    assert await detect(telemetry, page) is PageState.QUALIFICATION_CODE


async def test_detect_page_state_qualification_block_without_visible_input(
    telemetry: TimelineRecorder,
) -> None:
    """會員碼區塊常駐於模板但整塊隱藏；沒有可見輸入框就不是資格審查狀態。"""
    hidden = QUALIFICATION_CODE_HTML.replace(
        "<input type='text' ng-model='code'>",
        "<input type='text' ng-model='code' hidden='hidden'>",
    )
    page = FakePage(hidden.replace("</div></div>", "</div>" + TICKET_SELECTION_HTML))
    assert await detect(telemetry, page) is PageState.TICKET_SELECTION


async def test_detect_page_state_form_filling_outranks_the_inline_quiz(
    telemetry: TimelineRecorder,
) -> None:
    """問答題就長在聯絡人表單裡；判成獨立驗證題會漏填聯絡人與同意條款。"""
    page = FakePage(FORM_FILLING_HTML + STANDALONE_CAPTCHA_HTML)
    assert await detect(telemetry, page) is PageState.FORM_FILLING


async def test_detect_page_state_form_filling_from_countdown_notice(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div ng-switch-when='countingDown'>剩餘 09:59</div>")
    assert await detect(telemetry, page) is PageState.FORM_FILLING


async def test_detect_page_state_standalone_verification_challenge(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(STANDALONE_CAPTCHA_HTML)
    assert await detect(telemetry, page) is PageState.VERIFICATION_CHALLENGE


async def test_detect_page_state_ticket_selection_when_nothing_is_selected(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(TICKET_SELECTION_HTML)
    assert await detect(telemetry, page) is PageState.TICKET_SELECTION


async def test_detect_page_state_seat_selection_once_quantity_is_above_zero(
    telemetry: TimelineRecorder,
) -> None:
    """選票頁與劃位頁是同一個 AngularJS app：唯一的物理差別就是已選張數。"""
    page = FakePage(SEAT_SELECTION_HTML)
    assert await detect(telemetry, page) is PageState.SEAT_SELECTION


async def test_detect_page_state_selected_without_seat_button_stays_unknown(
    telemetry: TimelineRecorder,
) -> None:
    """已選票但配位／下一步按鈕還沒渲出來：是過渡暫態，不得臆造成劃位就緒。"""
    page = FakePage(
        TICKET_SELECTION_HTML.replace(
            "ticketModel.quantity' value='0'", "ticketModel.quantity' value='1'"
        )
    )
    assert await detect(telemetry, page) is PageState.UNKNOWN


async def test_detect_page_state_unknown(telemetry: TimelineRecorder) -> None:
    page = FakePage("<div>載入中</div>")
    assert await detect(telemetry, page) is PageState.UNKNOWN


async def test_detect_page_state_never_raises_on_a_broken_page(
    telemetry: TimelineRecorder,
) -> None:
    """狀態偵測跑在熱迴圈上：拋例外等於讓整場搶票在一個殘缺 DOM 上中止。"""
    page = FakePage("")
    page.url = ""
    assert await detect(telemetry, page) is PageState.UNKNOWN


async def test_read_selected_quantity_sums_every_unit(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(
        TICKET_SELECTION_HTML.replace(
            "ticketModel.quantity' value='0'", "ticketModel.quantity' value='3'"
        )
    )
    assert await make_adapter(telemetry).read_selected_quantity(page) == 3


async def test_read_selected_quantity_treats_unreadable_fields_as_zero(
    telemetry: TimelineRecorder,
) -> None:
    """讀不到就是讀不到，不得臆造選取狀態而讓迴圈以為已經選好票。"""
    page = FakePage(
        TICKET_SELECTION_HTML.replace(
            "ticketModel.quantity' value='0'", "ticketModel.quantity' value='--'"
        )
    )
    assert await make_adapter(telemetry).read_selected_quantity(page) == 0


# ------------------------------------------------------------- 失敗彈窗關閉


async def test_dismiss_failure_modal_clicks_close_and_records_the_reason(
    telemetry: TimelineRecorder,
) -> None:
    """彈窗文字是「這一輪為什麼搶輸」的唯一直接證據，不記下來就無從分析。"""
    page = FakePage(FAILURE_MODAL_HTML)
    assert await make_adapter(telemetry).dismiss_failure_modal(page) is True
    assert page.clicks
    assert "別人搶先一步" in marks(telemetry, FAILURE_MODAL_MARK)[0].detail["text"]


async def test_dismiss_failure_modal_reports_a_missing_close_button(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div class='modal in'><div>已無可配座位</div></div>")
    assert await make_adapter(telemetry).dismiss_failure_modal(page) is False
    assert not page.clicks
    assert "已無可配座位" in marks(telemetry, FAILURE_MODAL_MISSING_MARK)[0].detail["text"]


async def test_dismiss_failure_modal_truncates_a_runaway_modal_body(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(
        "<div class='modal in'><div>" + ("長" * 500) + "</div>"
        "<button class='close'>×</button></div>"
    )
    await make_adapter(telemetry).dismiss_failure_modal(page)
    assert len(marks(telemetry, FAILURE_MODAL_MARK)[0].detail["text"]) == 200


# ----------------------------------------------------------- 決策套用（apply）


def ticket_decision(
    *, index: int = 0, name: str = "全票", quantity: int = 1
) -> TicketDecision:
    return TicketDecision(
        status="SELECTED",
        option=TicketOption(
            index=index, name=name, price=3200, available=True, remaining=None
        ),
        quantity=quantity,
        matched_priority=TicketPriority(price=3200),
        fallback_used=False,
        trace=("test",),
    )


async def test_apply_ticket_decision_applies_the_decision_it_was_given(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(TICKET_SELECTION_HTML)
    adapter = make_adapter(telemetry)
    decision = ticket_decision(quantity=2)
    ok, reason = await adapter.apply_ticket_decision(page, decision)
    assert (ok, reason) == (True, REASON_SELECTED)
    quantity = page.locator("input[ng-model='ticketModel.quantity']").first
    assert await quantity.input_value() == "2"
    assert await page.locator("#person_agree_terms").first.is_checked()
    assert adapter.last_ticket_decision is decision
    assert marks(telemetry, "ticket_decision")[0].detail["status"] == "SELECTED"


async def test_apply_ticket_decision_does_not_redecide(
    telemetry: TimelineRecorder,
) -> None:
    """套用層重新決策，降級鏈路就會拿到第二份決策而反覆選到已搶輸的同一張票。"""
    html = TICKET_SELECTION_HTML.replace(
        "</div></div>",
        "</div><div class='ticket-unit'><div class='ticket-name'>搖滾區</div>"
        "<div class='ticket-price'>NT$ 3,800</div>"
        "<button class='btn-default plus' ng-click='quantityBtnClick(1)'></button>"
        "<input type='text' ng-model='ticketModel.quantity' value='0'>"
        "</div></div>",
        1,
    )
    page = FakePage(html)
    # 偏好指向 3200 的全票，但呼叫端已決定要第 1 順位以外的搖滾區。
    ok, _ = await make_adapter(telemetry).apply_ticket_decision(
        page, ticket_decision(index=1, name="搖滾區")
    )
    assert ok is True
    values = [
        await locator.input_value()
        for locator in await page.locator(
            "input[ng-model='ticketModel.quantity']"
        ).all()
    ]
    assert values == ["0", "1"]


async def test_apply_ticket_decision_refuses_a_sold_out_decision(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(TICKET_SELECTION_HTML)
    decision = TicketDecision(
        status="SOLD_OUT",
        option=None,
        quantity=1,
        matched_priority=None,
        fallback_used=False,
        trace=(),
    )
    ok, reason = await make_adapter(telemetry).apply_ticket_decision(page, decision)
    assert (ok, reason) == (False, REASON_SOLD_OUT)
    assert not page.clicks


async def test_apply_ticket_decision_rejects_a_stale_index(
    telemetry: TimelineRecorder,
) -> None:
    """決策與套用之間頁面重渲：拿舊索引點下去會買到別人的票種。"""
    page = FakePage(TICKET_SELECTION_HTML)
    ok, reason = await make_adapter(telemetry).apply_ticket_decision(
        page, ticket_decision(index=7)
    )
    assert (ok, reason) == (False, REASON_QUANTITY_MISMATCH)
    assert not page.clicks
    assert marks(telemetry, "ticket_unit_index_out_of_range")[0].detail["units"] == 1


async def test_apply_ticket_decision_without_any_ticket_unit(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div id='registrationsNewApp'></div>")
    ok, reason = await make_adapter(telemetry).apply_ticket_decision(
        page, ticket_decision()
    )
    assert (ok, reason) == (False, REASON_NO_TICKET_UNITS)


async def test_apply_ticket_decision_without_a_plus_button(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(
        TICKET_SELECTION_HTML.replace(
            "<button class='btn-default plus' ng-click='quantityBtnClick(1)'></button>",
            "",
        )
    )
    ok, reason = await make_adapter(telemetry).apply_ticket_decision(
        page, ticket_decision()
    )
    assert (ok, reason) == (False, REASON_PLUS_BUTTON_MISSING)


async def test_apply_ticket_decision_fails_closed_on_quantity_readback(
    telemetry: TimelineRecorder,
) -> None:
    """AngularJS model 沒跟上 DOM 時寧可失敗，也不靜默送出 0 張。"""
    page = FakePage(TICKET_SELECTION_HTML)
    page.quantity_step = 0
    ok, reason = await make_adapter(telemetry).apply_ticket_decision(
        page, ticket_decision(quantity=2)
    )
    assert (ok, reason) == (False, REASON_QUANTITY_MISMATCH)
    assert marks(telemetry, "quantity_readback_mismatch")[0].detail["actual"] == "0"


async def test_apply_ticket_decision_needs_the_terms_checkbox(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(
        TICKET_SELECTION_HTML.replace("id='person_agree_terms'", "id='other_terms'")
    )
    ok, reason = await make_adapter(telemetry).apply_ticket_decision(
        page, ticket_decision()
    )
    assert (ok, reason) == (False, REASON_TERMS_NOT_ACCEPTED)


async def test_apply_ticket_decision_never_unchecks_accepted_terms(
    telemetry: TimelineRecorder,
) -> None:
    """降級重選時再點一次，等於把原本合法的同意狀態切回未勾。"""
    page = FakePage(
        TICKET_SELECTION_HTML.replace(
            "id='person_agree_terms'", "id='person_agree_terms' checked='checked'"
        )
    )
    ok, _ = await make_adapter(telemetry).apply_ticket_decision(page, ticket_decision())
    assert ok is True
    assert "#person_agree_terms" not in page.clicks
    assert await page.locator("#person_agree_terms").first.is_checked()


async def test_select_tickets_delegates_to_the_same_apply_path(
    telemetry: TimelineRecorder,
) -> None:
    """兩條入口必須產生同一份決策，否則 wrapper 與迴圈會選到不同的票。"""
    page = FakePage(TICKET_SELECTION_HTML)
    adapter = make_adapter(telemetry)
    ok, reason = await adapter.select_tickets(page, preference(quantity=1))
    expected = decide_ticket(
        await adapter.read_registration_tickets(FakePage(TICKET_SELECTION_HTML)),
        preference(quantity=1),
    )
    assert (ok, reason) == (True, REASON_SELECTED)
    assert adapter.last_ticket_decision is not None
    assert adapter.last_ticket_decision.option is not None
    assert expected.option is not None
    assert adapter.last_ticket_decision.option.name == expected.option.name


# -------------------------------------------------------------- 數量歸零回讀

TWO_UNIT_HTML = (
    "<div id='registrationsNewApp'><div class='ticket-list'>"
    "<div class='ticket-unit'><div class='ticket-name'>全票</div>"
    "<button class='btn-default plus'></button>"
    "<button class='btn-default minus'></button>"
    "<input type='text' ng-model='ticketModel.quantity' value='2'>"
    "</div>"
    "<div class='ticket-unit'><div class='ticket-name'>搖滾區</div>"
    "<button class='btn-default plus'></button>"
    "<button class='btn-default minus'></button>"
    "<input type='text' ng-model='ticketModel.quantity' value='1'>"
    "</div></div></div>"
)


async def test_reset_ticket_quantities_zeroes_every_unit(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(TWO_UNIT_HTML)
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is True
    values = [
        await locator.input_value()
        for locator in await page.locator(
            "input[ng-model='ticketModel.quantity']"
        ).all()
    ]
    assert values == ["0", "0"]
    mark = marks(telemetry, RESET_MARK)[-1].detail
    assert (mark["ok"], mark["units"]) == (True, 2)


async def test_reset_ticket_quantities_leaves_already_zero_units_alone(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage(TICKET_SELECTION_HTML)
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is True
    assert not page.clicks


async def test_reset_ticket_quantities_skips_sold_out_units_without_a_field(
    telemetry: TimelineRecorder,
) -> None:
    """售完的票種不長數量欄位，本來就沒有東西要歸零——不該因此判定失敗。"""
    html = TWO_UNIT_HTML.replace(
        "<input type='text' ng-model='ticketModel.quantity' value='1'>", "售完"
    )
    page = FakePage(html)
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is True
    assert marks(telemetry, RESET_MARK)[-1].detail["units"] == 1


async def test_reset_ticket_quantities_without_any_unit(
    telemetry: TimelineRecorder,
) -> None:
    page = FakePage("<div id='registrationsNewApp'></div>")
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is False
    assert marks(telemetry, RESET_MARK)[-1].detail["reason"] == REASON_NO_TICKET_UNITS


async def test_reset_ticket_quantities_without_any_quantity_field(
    telemetry: TimelineRecorder,
) -> None:
    """有票種卻一個數量欄位都沒有：頁面形狀變了，不該臆測「歸零成功」。"""
    html = TWO_UNIT_HTML.replace("type='text' ng-model='ticketModel.quantity'", "type='hidden'")
    page = FakePage(html)
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is False
    assert (
        marks(telemetry, RESET_MARK)[-1].detail["reason"]
        == RESET_REASON_NO_QUANTITY_FIELD
    )


async def test_reset_ticket_quantities_fails_when_the_minus_button_is_gone(
    telemetry: TimelineRecorder,
) -> None:
    html = TWO_UNIT_HTML.replace("<button class='btn-default minus'></button>", "", 1)
    page = FakePage(html)
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is False
    detail = marks(telemetry, RESET_MARK)[-1].detail
    assert (detail["reason"], detail["unit"]) == (RESET_REASON_MINUS_MISSING, 0)


async def test_reset_ticket_quantities_fails_on_an_unreadable_quantity(
    telemetry: TimelineRecorder,
) -> None:
    html = TWO_UNIT_HTML.replace("value='2'", "value='二'")
    page = FakePage(html)
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is False
    assert marks(telemetry, RESET_MARK)[-1].detail["reason"] == RESET_REASON_UNREADABLE


async def test_reset_ticket_quantities_fails_closed_when_readback_is_not_zero(
    telemetry: TimelineRecorder,
) -> None:
    """帶著殘留數量選下一張票，比完全不選更糟。"""
    page = FakePage(TWO_UNIT_HTML)
    page.quantity_step = 0
    assert await make_adapter(telemetry).reset_ticket_quantities(page) is False
    detail = marks(telemetry, RESET_MARK)[-1].detail
    assert (detail["reason"], detail["actual"]) == (RESET_REASON_NOT_ZERO, "2")
