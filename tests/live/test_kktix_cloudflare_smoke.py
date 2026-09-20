# pyright: reportArgumentType=false
"""實站唯讀驗證：Cloudflare 挑戰偵測與被動寬限 smoke 測試。

本檔只讀，不登入、不填表、不送出；命中挑戰不繞過。
僅以被動方式探測頁面，驗證判讀與寬限等待期間絕無任何主動點擊或互動行為。
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from adapters.payment import MockPaymentProvider, PaymentOutcome
from adapters.ticketing.kktix.adapter import (
    CloudflareChallengeError,
    KKTIXAdapter,
    KKTIXPageKind,
)
from telemetry.timeline import TimelineRecorder

pytestmark = pytest.mark.live


class PageInteractionTracker:
    """包裝真實 Page，記錄所有互動嘗試以機械化證明被動等待期間絕無主動操作。"""

    def __init__(self, target: Any) -> None:
        self._target = target
        self.clicks: list[Any] = []
        self.fills: list[Any] = []
        self.gotos: list[Any] = []
        self.evaluates: list[Any] = []
        self.content_calls = 0

    async def content(self) -> str:
        self.content_calls += 1
        return await self._target.content()

    async def click(self, *args: Any, **kwargs: Any) -> Any:
        self.clicks.append((args, kwargs))
        return await self._target.click(*args, **kwargs)

    async def fill(self, *args: Any, **kwargs: Any) -> Any:
        self.fills.append((args, kwargs))
        return await self._target.fill(*args, **kwargs)

    async def goto(self, *args: Any, **kwargs: Any) -> Any:
        self.gotos.append((args, kwargs))
        return await self._target.goto(*args, **kwargs)

    async def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        self.evaluates.append((args, kwargs))
        return await self._target.evaluate(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


async def test_probe_page_reports_challenge_without_raising(
    live_page: Any, live_event_url: str
) -> None:
    telemetry = TimelineRecorder()
    adapter = KKTIXAdapter(
        telemetry=telemetry,
        payment=MockPaymentProvider(simulate=PaymentOutcome.CHECKPOINT_REACHED),
        timeout_ms=8000,
    )
    kind = await adapter.probe_page(live_page, live_event_url)
    assert isinstance(kind, KKTIXPageKind)
    if kind is KKTIXPageKind.CHALLENGE:
        print(f"[info] {live_event_url} 目前處於 CHALLENGE 狀態")


async def test_guard_cloudflare_grace_never_touches_the_page(
    live_page: Any, live_event_url: str
) -> None:
    telemetry = TimelineRecorder()
    adapter = KKTIXAdapter(
        telemetry=telemetry,
        payment=MockPaymentProvider(simulate=PaymentOutcome.CHECKPOINT_REACHED),
        timeout_ms=8000,
        challenge_grace_s=6.0,
        challenge_poll_s=2.0,
    )
    tracked_page = PageInteractionTracker(live_page)
    with contextlib.suppress(CloudflareChallengeError):
        await adapter._guard_cloudflare(tracked_page, "live_smoke")

    assert tracked_page.clicks == []
    assert tracked_page.fills == []
    assert tracked_page.gotos == []
    assert tracked_page.evaluates == []
