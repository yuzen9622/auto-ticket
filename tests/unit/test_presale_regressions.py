"""Presale boundary regressions: real adapter decisions, no navigation stubs."""

from unittest.mock import AsyncMock, patch

import pytest

from adapters.payment import MockPaymentProvider
from telemetry.timeline import TimelineRecorder
from adapters.ticketing.ibon.adapter import IbonAdapter
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from adapters.ticketing.page_state import PageState
from adapters.ticketing.tixcraft.adapter import TixcraftAdapter
from tests.fake_page import FakePage, load_page_html


@pytest.mark.parametrize("adapter", [KKTIXAdapter, TixcraftAdapter, IbonAdapter])
async def test_queue_probe_and_navigation_preserve_page(adapter):
    page = FakePage(
        '<div id="waiting-room">queue-it</div>', url="https://queue-it.net/wait"
    )
    impl = adapter(
        timeout_ms=20, telemetry=TimelineRecorder(), payment=MockPaymentProvider()
    )
    assert (
        await impl.probe_page(page, "https://example.com/events/demo")
    ).value == "QUEUE"
    await impl.navigate_to_event(page, "https://example.com/events/demo")
    assert page.goto_urls == []


@pytest.mark.parametrize(
    "button", ["", '<button class="btn-default plus" disabled>+</button>']
)
async def test_kktix_unopened_rows_are_not_sold_out_or_buyable(button):
    html = f'<div id="registrationsNewApp"><div class="ticket-list"><div class="ticket-unit"><span class="ticket-name">普通票</span><span class="ticket-price">100</span>{button}</div></div></div>'
    page = FakePage(html, url="https://kktix.com/events/demo/registrations/new")
    adapter = KKTIXAdapter(
        timeout_ms=20, telemetry=TimelineRecorder(), payment=MockPaymentProvider()
    )
    assert not await adapter.detect_sale_opened(page, 20)
    assert not (await adapter.read_registration_tickets(page))[0].available
    assert (await adapter.detect_page_state(page)).value == "WAITING_FOR_SALE"


async def test_kktix_angular_shell_stays_on_registration():
    page = FakePage(
        "<h1 class='event-title'>Demo</h1><div id='registrationsNewApp'>{{'new.i_read_and_agree_to'}}</div>",
        url="https://kktix.com/events/demo/registrations/new",
    )
    adapter = KKTIXAdapter(
        timeout_ms=20, telemetry=TimelineRecorder(), payment=MockPaymentProvider()
    )
    await adapter.navigate_to_event(page, "https://kktix.com/events/demo")
    assert page.goto_urls == []
    assert (await adapter.probe_page(page)).value == "LOADING"


async def test_tixcraft_session_id_and_ambiguous_name():
    page = FakePage(load_page_html("tixcraft_game_list.html"))
    adapter = TixcraftAdapter(timeout_ms=20)
    assert (await adapter._pick_session_url(page, "id:23083")).endswith("/23083")
    assert await adapter._pick_session_url(page, "Bruno Mars") is None
    assert await adapter._pick_session_url(page, "id:missing") is None
    page.soup.select('tr[data-key="23083"] button')[0].decompose()
    assert await adapter._pick_session_url(page, "id:23083") is None


async def test_ibon_target_unopened_does_not_fall_back():
    sessions = [
        dict(name="A", can_buy=True, purchase_url="https://orders.ibon.com.tw/A"),
        dict(name="B", can_buy=False, purchase_url=None),
    ]
    with patch(
        "adapters.ticketing.ibon.resolver.IbonEventResolver.fetch_sessions",
        AsyncMock(return_value=sessions),
    ):
        adapter = IbonAdapter(timeout_ms=20)
        assert (
            await adapter._resolve_session_url(
                "https://ticket.ibon.com.tw/ActivityInfo/Details/demo", "B"
            )
            is None
        )


async def test_ibon_empty_zone_page_waits_for_sale():
    page = FakePage(
        "<html><body>尚未開放</body></html>",
        url="https://orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx",
    )
    assert (
        await IbonAdapter(timeout_ms=20).detect_page_state(page)
    ).value == "WAITING_FOR_SALE"


@pytest.mark.parametrize("adapter", [KKTIXAdapter, TixcraftAdapter, IbonAdapter])
async def test_warmup_preserves_queue_even_without_login(tmp_path, adapter):
    from tests.unit.test_purchase_orchestrator import build, _ctx

    impl = adapter(
        timeout_ms=20, telemetry=TimelineRecorder(), payment=MockPaymentProvider()
    )
    orchestrator, _, browser, _ = build(tmp_path, impl)
    page = FakePage(
        '<h1 id="waiting-room">排隊中</h1>', url="https://qa.queue-it.net/wait"
    )
    orchestrator._rt.page = page
    await orchestrator._check_session(_ctx())
    await orchestrator._navigate_page(_ctx())
    await orchestrator._enter_ready(_ctx())
    await orchestrator._advance_from_standby(page)
    assert page.goto_urls == []
    assert orchestrator._rt.queue_seen


async def test_ibon_busy_zone_page_is_loading():
    page = FakePage(
        '<div aria-busy="true">Loading</div>',
        url="https://orders.ibon.com.tw/UTK0201_000.aspx",
    )
    assert await IbonAdapter(timeout_ms=20).detect_page_state(page) is PageState.LOADING


async def test_kktix_explicit_sold_out_remains_terminal_ticket_evidence():
    page = FakePage(
        '<div id="registrationsNewApp"><div class="ticket-list"><div class="ticket-unit"><span class="ticket-name">普通票 已售完</span><span class="ticket-price">100</span></div></div></div>'
    )
    adapter = KKTIXAdapter(
        timeout_ms=20, telemetry=TimelineRecorder(), payment=MockPaymentProvider()
    )
    assert await adapter.detect_page_state(page) is PageState.TICKET_SELECTION
    assert not (await adapter.read_registration_tickets(page))[0].available


async def test_kktix_disabled_fieldset_is_not_buyable():
    page = FakePage(
        '<div id="registrationsNewApp"><div class="ticket-list"><div class="ticket-unit"><fieldset disabled><button class="btn-default plus">+</button></fieldset></div></div></div>'
    )
    adapter = KKTIXAdapter(
        timeout_ms=20, telemetry=TimelineRecorder(), payment=MockPaymentProvider()
    )
    assert not (await adapter.read_registration_tickets(page))[0].available
