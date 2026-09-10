"""實站唯讀驗證：偵測 selector 漂移，回填 fixture 用的真實結構。

本檔**只讀**：導航、讀取票種快照、比對選擇器順位，然後關閉。
不填表、不選位、不送出訂單、不觸碰付款。
"""

from __future__ import annotations

from typing import Any

import pytest

from adapters.payment import MockPaymentProvider, PaymentOutcome
from adapters.ticketing.kktix.adapter import KKTIXAdapter
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


async def test_event_page_loads_without_challenge(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter
) -> None:
    assert await adapter.navigate_to_event(live_page, live_event_url) is True
    marker = contains_cloudflare_challenge(await live_page.content())
    assert marker is None, f"命中人機驗證挑戰：{marker}"


async def test_ticket_snapshot_is_readable(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter
) -> None:
    await adapter.navigate_to_event(live_page, live_event_url)
    options = await adapter.read_ticket_options(live_page)
    assert options, "讀不到任何票種單元：頁面結構可能已改版"
    assert all(option.name for option in options)
    assert all(option.price >= 0 for option in options)


async def test_first_rank_selectors_still_match(
    live_page: Any, live_event_url: str, adapter: KKTIXAdapter, telemetry: TimelineRecorder
) -> None:
    await adapter.navigate_to_event(live_page, live_event_url)
    await adapter.read_ticket_options(live_page)
    drift = [
        (e.detail.get("field"), e.detail.get("selector"), e.detail.get("rank"))
        for e in telemetry.events()
        if e.name == SELECTOR_FALLBACK_MARK
    ]
    assert not drift, f"選擇器已漂移到後備順位：{drift}"
