"""實站唯讀驗證（第一層）：公開活動主頁，**不需要登入**。

只做：導航 -> 判斷頁面種類 -> 讀票種表格 -> 比對選擇器順位 -> 關閉。
不填表、不選位、不送出任何東西。
"""

from __future__ import annotations

from typing import Any

import pytest

from adapters.payment import MockPaymentProvider, PaymentOutcome
from adapters.ticketing.kktix.adapter import KKTIXAdapter, KKTIXPageKind
from adapters.ticketing.kktix.dom import (
    SELECTOR_FALLBACK_MARK,
    contains_cloudflare_challenge,
)
from telemetry.timeline import TimelineRecorder

pytestmark = pytest.mark.live


@pytest.fixture
def telemetry() -> TimelineRecorder:
    return TimelineRecorder()


@pytest.fixture
def adapter(telemetry: TimelineRecorder) -> KKTIXAdapter:
    return KKTIXAdapter(
        telemetry=telemetry,
        payment=MockPaymentProvider(simulate=PaymentOutcome.CHECKPOINT_REACHED),
        timeout_ms=8000,
    )


def drift_report(telemetry: TimelineRecorder) -> list[tuple[Any, Any, Any]]:
    return [
        (e.detail.get("field"), e.detail.get("selector"), e.detail.get("rank"))
        for e in telemetry.events()
        if e.name == SELECTOR_FALLBACK_MARK
    ]


async def test_event_page_loads_without_challenge(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter
) -> None:
    assert await adapter.navigate_to_event(live_page, live_event_url) is True
    marker = contains_cloudflare_challenge(await live_page.content())
    assert marker is None, f"命中人機驗證挑戰：{marker}"


async def test_url_is_actually_an_event_page(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter
) -> None:
    await adapter.navigate_to_event(live_page, live_event_url)
    kind = await adapter.detect_page_kind(live_page)
    assert kind is KKTIXPageKind.EVENT, (
        f"{live_event_url} 判定為 {kind.value}；本層要的是公開活動主頁"
    )


async def test_event_page_ticket_table_is_readable(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter
) -> None:
    await adapter.navigate_to_event(live_page, live_event_url)
    options = await adapter.read_event_page_tickets(live_page)
    assert options, "讀不到任何票種列：主頁票種表格結構可能已改版"
    assert all(option.name for option in options)
    assert all(option.status_text for option in options)


async def test_event_page_first_rank_selectors_still_match(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter, telemetry: TimelineRecorder
) -> None:
    await adapter.navigate_to_event(live_page, live_event_url)
    await adapter.read_event_page_tickets(live_page)
    drift = drift_report(telemetry)
    assert not drift, f"選擇器已漂移到後備順位：{drift}"
