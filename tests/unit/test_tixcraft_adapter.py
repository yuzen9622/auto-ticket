# pyright: reportArgumentType=false
"""拓元 (Tixcraft) Adapter 單元測試。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.ticketing.page_state import (
    CloudflareChallengeError,
    PageKind,
    PageState,
    REASON_SELECTED,
)
from adapters.ticketing.tixcraft.adapter import TixcraftAdapter
from adapters.verification.base import (
    VerificationResult,
)
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
    ) -> None:
        self.name = name
        self._text = text
        self._visible = visible
        self._value = value
        self._checked = checked
        self._attrs = attrs or {}
        self._children = children or []
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
    def __init__(self, url: str = "https://tixcraft.com", html: str = "") -> None:
        self.url = url
        self._html = html
        self.locators: dict[str, MockLocator] = {}
        self.goto_urls: list[str] = []
        self.go_back_count = 0
        self.wait_urls: list[str] = []

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
        pass

    def locator(self, selector: str) -> MockLocator:
        if selector in self.locators:
            return self.locators[selector]
        for key, loc in self.locators.items():
            if selector in key or key in selector:
                return loc
        return MockLocator(selector, visible=False)


def test_tixcraft_probe_page() -> None:
    adapter = TixcraftAdapter()

    # Detail / Game -> EVENT
    page = MockPage(url="https://tixcraft.com/activity/detail/24_test")
    res = pytest.importorskip("asyncio").run(adapter.probe_page(page))
    assert res == PageKind.EVENT

    page = MockPage(url="https://tixcraft.com/activity/game/24_test")
    res = pytest.importorskip("asyncio").run(adapter.probe_page(page))
    assert res == PageKind.EVENT

    # Area / Ticket / Verify -> REGISTRATION
    page = MockPage(url="https://tixcraft.com/ticket/area/24_test/1001")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.REGISTRATION

    page = MockPage(url="https://tixcraft.com/ticket/ticket/24_test/1001/1")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.REGISTRATION

    page = MockPage(url="https://tixcraft.com/ticket/verify/24_test/1001")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.REGISTRATION

    # Login
    page = MockPage(url="https://tixcraft.com/login")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.LOGIN

    # Cloudflare
    page = MockPage(url="https://tixcraft.com/ticket/area", html="請啟用 JavaScript 與 Cookie 以繼續")
    assert pytest.importorskip("asyncio").run(adapter.probe_page(page)) == PageKind.CHALLENGE


def test_tixcraft_ticket_page_is_form_filling_regardless_of_select_value() -> None:
    adapter = TixcraftAdapter()
    html = load_fixture("tixcraft_ticket.html")

    # /ticket/ticket 頁面無論 select 初值為何，一律判定為 FORM_FILLING
    page = MockPage(url="https://tixcraft.com/ticket/ticket/24_test/1001/1", html=html)
    state = pytest.importorskip("asyncio").run(adapter.detect_page_state(page))
    assert state == PageState.FORM_FILLING


def test_tixcraft_queue_does_not_navigate() -> None:
    adapter = TixcraftAdapter()

    # Queue-It 等候室
    page = MockPage(url="https://tixcraft.queue-it.net/?c=tixcraft&e=concert")
    state = pytest.importorskip("asyncio").run(adapter.detect_page_state(page))
    assert state == PageState.QUEUE
    # 絕不觸發任何導航
    assert page.goto_urls == []
    assert page.go_back_count == 0


def test_tixcraft_detect_page_state_modal_and_race_cloudflare() -> None:
    adapter = TixcraftAdapter()

    # 售完 Modal
    page = MockPage(
        url="https://tixcraft.com/ticket/ticket/1",
        html='<div class="modal show"><div class="modal-body">此區已售完</div></div>',
    )
    modal_loc = MockLocator(".modal.show", text="此區已售完", visible=True)
    page.locators[".modal.show"] = modal_loc
    page.locators[".modal.in, .bootbox.modal, .modal.show, #msg-modal"] = modal_loc
    state = pytest.importorskip("asyncio").run(adapter.detect_page_state(page))
    assert state == PageState.FAILURE_MODAL

    # 開賣後遇 Cloudflare 直接拋出 fail-closed 例外
    cf_page = MockPage(
        url="https://tixcraft.com/ticket/area",
        html="正在執行安全驗證",
    )
    with pytest.raises(CloudflareChallengeError):
        pytest.importorskip("asyncio").run(adapter.detect_page_state(cf_page))


def test_tixcraft_quantity_decision_propagation() -> None:
    pref = TicketPreference(
        quantity=2,
        priorities=[TicketPriority(price=4800, ticket_name_pattern="特A區")],
    )
    adapter = TixcraftAdapter(ticket_preference=pref)

    # 1. 決策張數設為 4
    decision = TicketDecision(
        status="SELECTED",
        option=TicketOption(0, "特A區", 4800, True, 20, "特A區 4800"),
        quantity=4,
        matched_priority=None,
        fallback_used=False,
        trace=(),
    )
    page = MockPage(url="https://tixcraft.com/ticket/area/1")
    zone_link = MockLocator("zone_a", text="特A區 4800", visible=True)
    page.locators[".zone a, ul.area-list a"] = MockLocator("zones", children=[zone_link])

    ok, reason = pytest.importorskip("asyncio").run(adapter.apply_ticket_decision(page, decision))
    assert ok is True
    assert reason == REASON_SELECTED
    assert adapter._target_quantity == 4
    assert zone_link.clicked == 1

    # 2. fill_contact_form 選到 4 張
    ticket_page = MockPage(url="https://tixcraft.com/ticket/ticket/1")
    select_loc = MockLocator("select", visible=True)
    agree_loc = MockLocator("agree", visible=True, checked=False)
    ticket_page.locators["select[id*='TicketForm_ticketPrice_'], .mobile-select"] = select_loc
    ticket_page.locators["#TicketForm_agree"] = agree_loc

    fill_ok = pytest.importorskip("asyncio").run(
        adapter.fill_contact_form(ticket_page, UserContactProfile(name="Test", phone="0912345678", email="test@example.com"))
    )
    assert fill_ok is True
    assert select_loc.selected == ["4"]
    assert agree_loc.checked_count == 1


def test_tixcraft_dismiss_failure_modal_returns_to_area() -> None:
    adapter = TixcraftAdapter()
    page = MockPage(url="https://tixcraft.com/ticket/ticket/1")

    close_btn = MockLocator("close_btn", visible=True)
    page.locators[".modal.in button.close, .bootbox button[data-bb-handler='ok'], .modal.show button.btn-primary"] = close_btn
    area_container = MockLocator("area_container", visible=True)
    page.locators[".zone, ul.area-list, #area-list"] = area_container

    dismiss_ok = pytest.importorskip("asyncio").run(adapter.dismiss_failure_modal(page))
    assert dismiss_ok is True
    assert close_btn.clicked == 1
    assert page.go_back_count == 1


def test_tixcraft_handle_verification_ocr() -> None:
    verification = MagicMock()
    verification.solve = AsyncMock(
        return_value=VerificationResult(
            provider="ddddocr",
            solved=True,
            answer="abcd",
            detail={"confidence": 0.95},
        )
    )
    adapter = TixcraftAdapter(verification=verification, ocr_expected_length=4)

    page = MockPage(url="https://tixcraft.com/ticket/ticket/1")
    img_loc = MockLocator("captcha_img", visible=True)
    input_loc = MockLocator("captcha_input", visible=True)
    page.locators["#TicketForm_verifyCode-image"] = img_loc
    page.locators["#TicketForm_verifyCode"] = input_loc

    solved = pytest.importorskip("asyncio").run(adapter.handle_verification(page))
    assert solved is True
    assert input_loc.filled == ["abcd"]
    assert verification.solve.call_count == 1


def test_tixcraft_handle_cloudflare() -> None:
    adapter = TixcraftAdapter()

    # Challenge active -> tries solve
    page = MockPage(html="正在執行安全驗證")
    cf_frame = MockLocator("cf_frame", visible=True)
    page.locators["iframe[src*='challenges.cloudflare.com']"] = cf_frame
    # 單次探測
    solved = pytest.importorskip("asyncio").run(adapter.handle_cloudflare(page))
    # 只要挑戰文字仍在，單次回傳 False
    assert solved is False

    # Challenge absent -> returns True
    clean_page = MockPage(html="<html><body>normal content</body></html>")
    assert pytest.importorskip("asyncio").run(adapter.handle_cloudflare(clean_page)) is True
