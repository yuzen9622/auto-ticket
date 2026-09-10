"""實站唯讀驗證（第二層）：購票登記頁，**需要已登入的 profile**。

登入不在本專案範圍內：請自行以同一個 `user_data_dir` 開有頭瀏覽器登入一次
（見 `conftest.py` 的說明）。本檔只讀頁面結構，**絕不**加減票數、勾選條款、
填寫表單或送出任何東西。
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


async def test_registration_page_loads_without_challenge(
    live_profile_page: Any, live_registration_url: str, adapter: KKTIXAdapter
) -> None:
    assert await adapter.navigate_to_event(live_profile_page, live_registration_url) is True
    marker = contains_cloudflare_challenge(await live_profile_page.content())
    assert marker is None, f"命中人機驗證挑戰：{marker}"


async def test_profile_is_logged_in_and_on_the_registration_page(
    live_profile_page: Any, live_registration_url: str, adapter: KKTIXAdapter
) -> None:
    await adapter.navigate_to_event(live_profile_page, live_registration_url)
    kind = await adapter.detect_page_kind(live_profile_page)
    hint = {
        KKTIXPageKind.UNKNOWN: "多半是該 profile 尚未登入而被導去登入頁",
        KKTIXPageKind.EVENT: "這是活動主頁，不是登記頁（網址要帶 /registrations/new）",
        KKTIXPageKind.ORDER: "已經是訂單頁；請改用尚未開始登記的活動，本套件不得動既有訂單",
    }.get(kind, "")
    assert kind is KKTIXPageKind.REGISTRATION, (
        f"判定為 {kind.value}（實際網址 {live_profile_page.url}）：{hint}"
    )


async def test_registration_ticket_units_are_readable(
    live_profile_page: Any, live_registration_url: str, adapter: KKTIXAdapter
) -> None:
    await adapter.navigate_to_event(live_profile_page, live_registration_url)
    options = await adapter.read_registration_tickets(live_profile_page)
    assert options, "讀不到任何票種單元：登記頁結構可能已改版，或尚未開賣"
    assert all(option.name for option in options)


async def test_registration_first_rank_selectors_still_match(
    live_profile_page: Any,
    live_registration_url: str,
    adapter: KKTIXAdapter,
    telemetry: TimelineRecorder,
) -> None:
    await adapter.navigate_to_event(live_profile_page, live_registration_url)
    await adapter.read_registration_tickets(live_profile_page)
    drift = [
        (e.detail.get("field"), e.detail.get("selector"), e.detail.get("rank"))
        for e in telemetry.events()
        if e.name == SELECTOR_FALLBACK_MARK
    ]
    assert not drift, f"選擇器已漂移到後備順位：{drift}"
