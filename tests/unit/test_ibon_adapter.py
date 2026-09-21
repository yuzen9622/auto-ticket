# pyright: reportArgumentType=false
"""ibon Adapter 單元測試。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.ticketing.ibon.adapter import IbonAdapter, normalize_ibon_url
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
        self.wait_urls: list[str] = []
        self.wait_load_states: list[str] = []

    async def content(self) -> str:
        return self._html

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_urls.append(url)
        self.url = url

    async def go_back(self, **kwargs: Any) -> None:
        self.go_back_count += 1

    async def wait_for_url(self, pattern: str, timeout: float | None = None) -> None:
        self.wait_urls.append(pattern)

    async def wait_for_load_state(self, state: str = "domcontentloaded", timeout: float | None = None) -> None:
        self.wait_load_states.append(state)

    def locator(self, selector: str) -> MockLocator:
        if selector in self.locators:
            return self.locators[selector]
        for key, loc in self.locators.items():
            if selector in key or key in selector:
                return loc
        return MockLocator(selector, visible=False)


def test_ibon_old_url_normalization() -> None:
    old_url = "https://ticket.ibon.com.tw/ActivityInfo/UTK0202_000.aspx?PERFORMANCE_PRICE_AREA_ID=A01"
    normalized = normalize_ibon_url(old_url)
    assert "UTK0201_000.aspx" in normalized
    assert "UTK0202" not in normalized


def test_ibon_probe_page() -> None:
    adapter = IbonAdapter()

    # Detail -> EVENT
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/Details/38001")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.EVENT

    # Area / Ticket -> REGISTRATION
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_000.aspx?PERFORMANCE_ID=1")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.REGISTRATION

    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_001.aspx?PERFORMANCE_PRICE_AREA_ID=A01")
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
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_000.aspx")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(page)) == PageState.TICKET_SELECTION

    # Form / Ticket page
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_001.aspx")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(page)) == PageState.FORM_FILLING

    # Qualification code
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_0.aspx")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(page)) == PageState.QUALIFICATION_CODE

    # Queue-It
    queue_page = MockPage(url="https://ibon.queue-it.net/?c=ibon&e=concert")
    assert pytest.importorskip("asyncio").run(adapter.detect_page_state(queue_page)) == PageState.QUEUE
    assert queue_page.goto_urls == []
    assert queue_page.go_back_count == 0

    # Failure modal
    modal_page = MockPage(
        url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_001.aspx",
        html='<div class="modal show"><div class="modal-body">已售完</div></div>',
    )
    modal_loc = MockLocator(".modal.show", text="已售完", visible=True)
    modal_page.locators[".modal.show, .modal.in, #myModal, .sweet-alert, div[class*='alert']"] = modal_loc
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
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_000.aspx")
    buy_btn = MockLocator("btnBuy", visible=True)
    page.locators["a[id*='btnBuy'], [id$='btnBuy'], table.table a.btn, input[value*='選購']"] = (
        MockLocator("btns", children=[buy_btn])
    )

    ok, reason = pytest.importorskip("asyncio").run(adapter.apply_ticket_decision(page, decision))
    assert ok is True
    assert reason == REASON_SELECTED
    assert adapter._target_quantity == 4
    assert buy_btn.clicked == 1

    # 2. fill_contact_form 選到 4 張
    ticket_page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_001.aspx")
    select_loc = MockLocator("select", visible=True)
    chk_seat = MockLocator("chkSeat", visible=True, checked=False)
    agree_loc = MockLocator("agree", visible=True, checked=False)
    ticket_page.locators["select[id*='ddlAmount'], [id$='ddlAmount'], table.table select"] = select_loc
    ticket_page.locators["#ctl00_ContentPlaceHolder1_chkSeat, [id$='chkSeat']"] = chk_seat
    ticket_page.locators["#ctl00_ContentPlaceHolder1_chkAgree, [id$='chkAgree']"] = agree_loc

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
    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_001.aspx")

    close_btn = MockLocator("close_btn", visible=True)
    page.locators[".modal.show button.close, .sweet-alert button.confirm, button[data-dismiss='modal'], button.btn-primary"] = close_btn
    table_loc = MockLocator("table", visible=True)
    page.locators["#ctl00_ContentPlaceHolder1_DataGrid1, #ctl00_ContentPlaceHolder1_divArea table, table.table"] = table_loc

    dismiss_ok = pytest.importorskip("asyncio").run(adapter.dismiss_failure_modal(page))
    assert dismiss_ok is True
    assert close_btn.clicked == 1
    assert page.go_back_count == 1


def test_ibon_read_registration_tickets() -> None:
    adapter = IbonAdapter()
    page = MockPage(
        url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_000.aspx",
        html=load_fixture("ibon_utk0201_000.html"),
    )
    row1 = MockLocator("row1", text="VIP特區 4500 熱賣中 選購", visible=True)
    row2 = MockLocator("row2", text="搖滾A區 3500 已售完", visible=True)
    row3 = MockLocator("row3", text="二樓看台區 2500 尚有空位 選購", visible=True)
    page.locators["#ctl00_ContentPlaceHolder1_DataGrid1 tbody tr, table.table tbody tr"] = (
        MockLocator("rows", children=[row1, row2, row3])
    )

    tickets = pytest.importorskip("asyncio").run(adapter.read_registration_tickets(page))
    assert len(tickets) == 3
    assert tickets[0].name == "VIP特區"
    assert tickets[0].price == 4500
    assert tickets[0].available is True

    assert tickets[1].name == "搖滾A區"
    assert tickets[1].price == 3500
    assert tickets[1].available is False  # 已售完

    assert tickets[2].name == "二樓看台區"
    assert tickets[2].price == 2500
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

    page = MockPage(url="https://ticket.ibon.com.tw/ActivityInfo/UTK0201_001.aspx")
    img_loc = MockLocator("captcha_img", visible=True)
    input_loc = MockLocator("captcha_input", visible=True)
    page.locators["#ctl00_ContentPlaceHolder1_imgVerify, [id$='imgVerify'], #imgVerify"] = img_loc
    page.locators["#ctl00_ContentPlaceHolder1_txtVerify, [id$='txtVerify'], #txtVerify"] = input_loc

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
