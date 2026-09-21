# pyright: reportArgumentType=false
"""ibon Adapter 單元測試。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.ticketing.ibon.adapter import IbonAdapter, normalize_ibon_url
from adapters.ticketing.ibon.selectors import IbonSelectors
from adapters.ticketing.page_state import (
    REASON_SELECTED,
    CloudflareChallengeError,
    PageKind,
    PageState,
)
from adapters.verification.base import VerificationResult
from domain.preference import TicketPreference, TicketPriority
from domain.task import UserContactProfile
from strategy.ticket_strategy import TicketDecision, TicketOption

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class MockLocator:
    def __init__(
        self,
        name: str = "loc",
        *,
        text: str = "",
        visible: bool = True,
        value: str = "",
        checked: bool = False,
        attrs: dict[str, str] | None = None,
        children: list[MockLocator] | None = None,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self._text = text
        self._visible = visible
        self._value = value
        self._checked = checked
        self._attrs = attrs or {}
        self._children = children or []
        self._on_click = on_click
        self.clicked = 0
        self.filled: list[str] = []
        self.selected: list[str] = []
        self.checked_count = 0

    @property
    def first(self) -> MockLocator:
        return self._children[0] if self._children else self

    def locator(self, selector: str) -> MockLocator:
        for child in self._children:
            if selector in child.name or child.name in selector:
                return child
        return MockLocator(selector, visible=False)

    async def count(self) -> int:
        """零等待探測會先問「有幾個」，mock 也要能回答，否則新的探測路徑等於沒被測到。"""
        return len(self._children) if self._children else 1

    def nth(self, index: int) -> MockLocator:
        if self._children:
            return self._children[index]
        return self if index == 0 else MockLocator("out-of-range", visible=False)

    async def all(self) -> list[MockLocator]:
        return list(self._children) if self._children else [self]

    async def is_visible(self) -> bool:
        return self._visible

    async def inner_text(self) -> str:
        return self._text

    async def input_value(self) -> str:
        return self._value

    async def is_checked(self) -> bool:
        return self._checked

    async def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    async def click(self, **kwargs: Any) -> None:
        self.clicked += 1
        if self._on_click is not None:
            self._on_click()

    async def fill(self, value: str, **kwargs: Any) -> None:
        self.filled.append(value)
        self._value = value

    async def select_option(self, value: str, **kwargs: Any) -> None:
        self.selected.append(value)
        self._value = value

    async def check(self, **kwargs: Any) -> None:
        self._checked = True
        self.checked_count += 1

    async def screenshot(self, **kwargs: Any) -> bytes:
        return f"screenshot-{self.name}-{self.clicked}".encode()

    async def wait_for(self, state: str = "visible", timeout: float | None = None) -> None:
        if not self._visible:
            raise RuntimeError(f"{self.name} not visible")


class MockPage:
    def __init__(self, url: str = "https://ticket.ibon.com.tw", html: str = "") -> None:
        self.url = url
        self._html = html
        self.locators: dict[str, MockLocator] = {}
        self.goto_urls: list[str] = []
        self.go_back_count = 0
        self.wait_urls: list[Any] = []
        self.wait_load_states: list[str] = []
        self.evaluated: list[tuple[str, Any]] = []

    async def content(self) -> str:
        return self._html

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_urls.append(url)
        self.url = url

    async def go_back(self, **kwargs: Any) -> None:
        self.go_back_count += 1

    async def wait_for_url(self, pattern: Any, timeout: float | None = None) -> None:
        self.wait_urls.append(pattern)

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        self.evaluated.append((expression, arg))
        return None

    async def wait_for_load_state(self, state: str = "domcontentloaded", timeout: float | None = None) -> None:
        self.wait_load_states.append(state)

    def locator(self, selector: str) -> MockLocator:
        if selector in self.locators:
            return self.locators[selector]
        for key, loc in self.locators.items():
            if selector in key or key in selector:
                return loc
        return MockLocator(selector, visible=False)


def test_ibon_url_is_not_rewritten() -> None:
    """UTK0202_ 是「選張數」那一頁，不是舊版票區頁。

    之前把它改寫成 UTK0201_000.aspx，等於每次選完票區就被推回票區頁，流程永遠
    走不到付款。
    """
    quantity_url = (
        "https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx"
        "?PERFORMANCE_ID=B0CB317N&GROUP_ID=&PERFORMANCE_PRICE_AREA_ID=B0CB7VJ3"
    )
    assert normalize_ibon_url(quantity_url) == quantity_url


def test_ibon_probe_page() -> None:
    adapter = IbonAdapter()

    # Detail -> EVENT
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/Details/38001")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.EVENT

    # Area / Ticket -> REGISTRATION
    page = MockPage(url="https://orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx?PERFORMANCE_ID=1")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.REGISTRATION

    page = MockPage(url="https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx?PERFORMANCE_PRICE_AREA_ID=A01")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.REGISTRATION

    # Login / loginhuiwan
    page = MockPage(url="https://ticket.ibon.com.tw/loginhuiwan.aspx")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.LOGIN

    # Cloudflare
    page = MockPage(url="https://ticket.ibon.com.tw", html="驗證您是人類")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.CHALLENGE


def test_ibon_detect_page_state() -> None:
    adapter = IbonAdapter()

    # Area page
    page = MockPage(url="https://orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(page)) == PageState.TICKET_SELECTION

    # Form / Ticket page
    page = MockPage(url="https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(page)) == PageState.FORM_FILLING

    # Qualification code
    page = MockPage(url="https://orders.ibon.com.tw/application/UTK02/UTK0201_0.aspx?rn=1")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(page)) == PageState.QUALIFICATION_CODE

    # Queue-It
    queue_page = MockPage(url="https://ibon.queue-it.net/?c=ibon&e=concert")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(queue_page)) == PageState.QUEUE
    assert queue_page.goto_urls == []
    assert queue_page.go_back_count == 0

    # Failure modal
    modal_page = MockPage(
        url="https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx",
        html='<div class="modal show"><div class="modal-body">已售完</div></div>',
    )
    modal_loc = MockLocator(".modal.show", text="已售完", visible=True)
    modal_page.locators[IbonSelectors.FAILURE_MODAL] = modal_loc
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(modal_page)) == PageState.FAILURE_MODAL

    # Cloudflare fail-closed
    cf_page = MockPage(html="正在執行安全驗證")
    with pytest.raises(CloudflareChallengeError):
        pytest.importorskip("asyncio").run(adapter.detect_page_state(cf_page))


def test_ibon_quantity_decision_propagation() -> None:
    pref = TicketPreference(
        quantity=2,
        priorities=[TicketPriority(price=4500, ticket_name_pattern="VIP特區")],
    )
    adapter = IbonAdapter(ticket_preference=pref)

    # 1. 決策張數設為 4
    decision = TicketDecision(
        status="SELECTED",
        option=TicketOption(0, "VIP特區", 4500, True, 10, "VIP特區 4500 熱賣中"),
        quantity=4,
        matched_priority=None,
        fallback_used=False,
        trace=(),
    )
    page = zone_page()

    ok, reason = pytest.importorskip("asyncio").run(adapter.apply_ticket_decision(page, decision))
    assert ok is True
    assert reason == REASON_SELECTED
    assert adapter._target_quantity == 4
    # `<area>` 是零尺寸元素點不動，必須照頁面自己的 Send() 導頁。
    assert len(page.evaluated) == 1
    assert page.evaluated[0][1] == ["0202", "B0CB317N", "A01", ""]

    # 2. fill_contact_form 選到 4 張
    ticket_page = MockPage(
        url="https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx?PERFORMANCE_ID=B0CB317N"
    )
    select_loc = MockLocator("select", visible=True)
    chk_seat = MockLocator("chkSeat", visible=True, checked=False)
    agree_loc = MockLocator("agree", visible=True, checked=False)
    ticket_page.locators[IbonSelectors.TICKET_SELECTS] = select_loc
    ticket_page.locators[IbonSelectors.NON_ADJACENT_SEAT_CHECKBOX] = chk_seat
    ticket_page.locators[IbonSelectors.AGREE_CHECKBOX] = agree_loc

    fill_ok = pytest.importorskip("asyncio").run(
        adapter.fill_contact_form(
            ticket_page,
            UserContactProfile(name="Test", phone="0912345678", email="test@example.com"),
        )
    )
    assert fill_ok is True
    assert select_loc.selected == ["4"]
    assert chk_seat.checked_count == 1
    assert agree_loc.checked_count == 1


def test_ibon_dismiss_failure_modal_returns_to_area() -> None:
    adapter = IbonAdapter()
    page = MockPage(
        url="https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx?PERFORMANCE_ID=B0CB317N"
    )

    close_btn = MockLocator("close_btn", visible=True)
    page.locators[IbonSelectors.FAILURE_MODAL_CLOSE] = close_btn
    table_loc = MockLocator("seatmap", visible=True)
    page.locators[IbonSelectors.ZONE_TABLE] = table_loc

    dismiss_ok = pytest.importorskip("asyncio").run(adapter.dismiss_failure_modal(page))
    assert dismiss_ok is True
    assert close_btn.clicked == 1
    assert page.go_back_count == 1


def area_locator(name: str, title: str, area_id: str) -> MockLocator:
    """座位圖上的一個票區。`<area>` 只有屬性可讀，沒有文字也量不到大小。"""
    return MockLocator(
        name,
        attrs={
            "title": title,
            "href": f"javascript:Send('0202', 'B0CB317N', '{area_id}', '');",
        },
    )


def zone_page() -> MockPage:
    page = MockPage(
        url="https://orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx?PERFORMANCE_ID=B0CB317N",
        html=load_fixture("ibon_utk0201_000.html"),
    )
    page.locators[IbonSelectors.ZONE_ROWS] = MockLocator(
        "areas",
        children=[
            area_locator("vip", "票區:VIP 票價：4,500 尚餘：熱賣中", "A01"),
            area_locator("rock", "票區:搖滾A區 票價：3500 尚餘：0", "A02"),
            area_locator("balcony", "票區:二樓看台區 票價：2500 尚餘：6", "A03"),
        ],
    )
    return page


def test_ibon_read_registration_tickets_from_seat_map() -> None:
    adapter = IbonAdapter()
    tickets = pytest.importorskip("asyncio").run(
        adapter.read_registration_tickets(zone_page())
    )

    assert len(tickets) == 3
    assert tickets[0].name == "VIP"
    assert tickets[0].price == 4500
    assert tickets[0].available is True
    # 「熱賣中」只代表有票，不公布剩餘量。
    assert tickets[0].remaining is None

    assert tickets[1].name == "搖滾A區"
    assert tickets[1].available is False  # 尚餘：0
    assert tickets[1].remaining == 0

    assert tickets[2].name == "二樓看台區"
    assert tickets[2].remaining == 6
    assert tickets[2].available is True


def test_ibon_handle_verification_ocr() -> None:
    verification = MagicMock()
    verification.solve = AsyncMock(
        return_value=VerificationResult(
            provider="ddddocr",
            solved=True,
            answer="5678",
            detail={"confidence": 0.98},
        )
    )
    adapter = IbonAdapter(verification=verification, ocr_expected_length=4)

    page = MockPage(
        url="https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx?PERFORMANCE_ID=B0CB317N"
    )
    img_loc = MockLocator("captcha_img", visible=True)
    input_loc = MockLocator("captcha_input", visible=True)
    page.locators[IbonSelectors.CAPTCHA_IMAGE] = img_loc
    page.locators[IbonSelectors.CAPTCHA_INPUT] = input_loc

    solved = pytest.importorskip("asyncio").run(adapter.handle_verification(page))
    assert solved is True
    assert input_loc.filled == ["5678"]


def test_ibon_login_retries_refreshed_captcha_after_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    verification = MagicMock()
    verification.solve = AsyncMock(
        side_effect=[
            VerificationResult(provider="ddddocr", solved=True, answer="WRONG1"),
            VerificationResult(provider="ddddocr", solved=True, answer="ABC123"),
        ]
    )
    adapter = IbonAdapter(verification=verification, ocr_max_retries=2)
    page = MockPage(
        url=(
            "https://huiwan.ibon.com.tw/huiwan/LoginHuiwan/UserLogin.aspx"
            "?taxid=775995263&targeturl=https://ticket.ibon.com.tw/login"
        )
    )

    user_input = MockLocator("#Mobile")
    password_input = MockLocator("#password")
    captcha_image = MockLocator("#validateCode")
    captcha_input = MockLocator("#boxWebCode")

    def complete_login_on_second_submit() -> None:
        if login_button.clicked == 2:
            page.url = "https://ticket.ibon.com.tw/"

    login_button = MockLocator("#login", on_click=complete_login_on_second_submit)
    page.locators.update(
        {
            "#Mobile": user_input,
            "#password": password_input,
            "#validateCode": captcha_image,
            "#boxWebCode": captcha_input,
            "#login": login_button,
        }
    )

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("adapters.ticketing.ibon.adapter.asyncio.sleep", no_sleep)

    logged_in = pytest.importorskip("asyncio").run(
        adapter.login(page, "0912345678", "secret")
    )

    assert logged_in is True
    assert verification.solve.await_count == 2
    assert captcha_input.filled == ["WRONG1", "ABC123"]
    assert login_button.clicked == 2
    assert page.wait_load_states == ["domcontentloaded"]


def test_ibon_login_returns_false_when_captcha_retries_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verification = MagicMock()
    verification.solve = AsyncMock(
        side_effect=[
            VerificationResult(provider="ddddocr", solved=True, answer="WRONG1"),
            VerificationResult(provider="ddddocr", solved=True, answer="WRONG2"),
        ]
    )
    adapter = IbonAdapter(verification=verification, ocr_max_retries=2)
    page = MockPage(
        url=(
            "https://huiwan.ibon.com.tw/huiwan/LoginHuiwan/UserLogin.aspx"
            "?taxid=775995263&targeturl=https://ticket.ibon.com.tw/login"
        )
    )
    login_button = MockLocator("#login")
    password_input = MockLocator("#password")
    page.locators.update(
        {
            "#Mobile": MockLocator("#Mobile"),
            "#password": password_input,
            "#validateCode": MockLocator("#validateCode"),
            "#boxWebCode": MockLocator("#boxWebCode"),
            "#login": login_button,
        }
    )

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("adapters.ticketing.ibon.adapter.asyncio.sleep", no_sleep)

    logged_in = pytest.importorskip("asyncio").run(
        adapter.login(page, "0912345678", "secret")
    )

    assert logged_in is False
    assert verification.solve.await_count == 2
    assert login_button.clicked == 2
    assert password_input.filled[-1] == ""


def test_ibon_login_rejects_same_host_error_page(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = IbonAdapter()
    page = MockPage(
        url="https://huiwan.ibon.com.tw/huiwan/LoginHuiwan/UserLogin.aspx"
    )
    password_input = MockLocator("#password")

    def redirect_to_error_page() -> None:
        page.url = "https://ticket.ibon.com.tw/account/login-error"

    page.locators.update(
        {
            "#Mobile": MockLocator("#Mobile"),
            "#password": password_input,
            "#login": MockLocator("#login", on_click=redirect_to_error_page),
        }
    )

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("adapters.ticketing.ibon.adapter.asyncio.sleep", no_sleep)

    logged_in = pytest.importorskip("asyncio").run(
        adapter.login(page, "0912345678", "secret")
    )

    assert logged_in is False
    assert password_input.filled[-1] == ""


def test_ibon_handle_cloudflare() -> None:
    adapter = IbonAdapter()

    page = MockPage(html="正在執行安全驗證")
    cf_frame = MockLocator("cf_frame", visible=True)
    page.locators["iframe[src*='challenges.cloudflare.com']"] = cf_frame
    solved = pytest.importorskip("asyncio").run(adapter.handle_cloudflare(page))
    assert solved is False

    clean_page = MockPage(html="<html><body>normal content</body></html>")
    assert pytest.importorskip("asyncio").run(adapter.handle_cloudflare(clean_page)) is True


def test_ibon_payment_page_is_payment_required() -> None:
    """UTK0206_ 是購票確認與付款頁；沒有這條判定就永遠走不到付款。"""
    adapter = IbonAdapter()
    page = MockPage(
        url="https://orders.ibon.com.tw/application/UTK02/UTK0206_.aspx"
    )
    state = pytest.importorskip("asyncio").run(adapter.detect_page_state(page))
    assert state == PageState.PAYMENT_REQUIRED


def test_ibon_quantity_page_is_form_filling() -> None:
    adapter = IbonAdapter()
    for url in (
        "https://orders.ibon.com.tw/application/UTK02/UTK0202_.aspx?PERFORMANCE_ID=B0C",
        "https://orders.ibon.com.tw/application/UTK02/UTK0205_.aspx?PERFORMANCE_ID=B0C",
        "https://orders.ibon.com.tw/application/UTK02/UTK0201_001.aspx?PERFORMANCE_ID=B0C",
    ):
        state = pytest.importorskip("asyncio").run(
            adapter.detect_page_state(MockPage(url=url))
        )
        assert state == PageState.FORM_FILLING, url


def test_ibon_login_redirect_is_guest_modal() -> None:
    adapter = IbonAdapter()
    page = MockPage(
        url="https://huiwan.ibon.com.tw/huiwan/LoginHuiwan/UserLogin.aspx?taxid=1"
    )
    state = pytest.importorskip("asyncio").run(adapter.detect_page_state(page))
    assert state == PageState.GUEST_MODAL
