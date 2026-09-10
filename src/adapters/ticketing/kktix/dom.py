"""KKTIX 頁面的 AngularJS 相容 DOM 操作。

KKTIX 登記頁由 AngularJS 驅動：直接 `fill()` / `click()` 有時不會更新 model，
送出時會拿到空值或 0。所有 DOM 互動集中在本模組，adapter 只呼叫這裡的函式。

選擇器候選清單的嘗試順序**即 `KKTIXSelectors` 的宣告順序**，不得重排。
一旦 fallback 到第 2 順位以後，記一筆 `selector_fallback` mark——
那是對方改版的早期訊號。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from adapters.ticketing.kktix.selectors import KKTIXSelectors
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page
else:
    Locator = Any
    Page = Any

DEFAULT_PROBE_TIMEOUT_MS = 2000
# 補送事件是「順手多做一件事」，不是主要動作：短逾時，失敗也不能拖垮流程。
DEFAULT_DISPATCH_TIMEOUT_MS = 1000
SELECTOR_FALLBACK_MARK = "selector_fallback"
DISPATCH_SKIPPED_MARK = "ng_dispatch_skipped"

_NG_DISPATCH = (
    "el => {"
    " el.dispatchEvent(new Event('input', { bubbles: true }));"
    " el.dispatchEvent(new Event('change', { bubbles: true }));"
    "}"
)


def candidate_selectors(selectors: str | Sequence[str]) -> tuple[str, ...]:
    """把 `KKTIXSelectors` 屬性正規化成候選清單，保持宣告順序。"""
    if isinstance(selectors, str):
        return (selectors,)
    return tuple(selectors)


async def first_visible(
    page: Page,
    selectors: str | Sequence[str],
    *,
    timeout_ms: int = DEFAULT_PROBE_TIMEOUT_MS,
    telemetry: TimelineRecorder | None = None,
    field: str = "",
) -> Locator | None:
    """依候選順序回傳第一個可見元素；全數落空回 `None`（**不拋例外**）。

    語意由呼叫端決定：有些欄位缺席是正常的（例如沒有驗證題），
    在這一層拋例外會逼所有呼叫端寫 try/except。
    """
    candidates = candidate_selectors(selectors)
    if not candidates:
        return None
    per_try = max(1, int(timeout_ms / len(candidates)))
    for rank, selector in enumerate(candidates, start=1):
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=per_try)
        except Exception:
            continue
        if rank > 1 and telemetry is not None:
            telemetry.record(
                TimelineEventType.MARK,
                SELECTOR_FALLBACK_MARK,
                field=field or selector,
                selector=selector,
                rank=rank,
                total=len(candidates),
            )
        return locator
    return None


async def ng_dispatch(
    locator: Locator, *, timeout_ms: int = DEFAULT_DISPATCH_TIMEOUT_MS
) -> None:
    """補送 `input` / `change` 事件以驅動 AngularJS 的 $digest。"""
    await locator.evaluate(_NG_DISPATCH, timeout=timeout_ms)


async def ng_click(
    page: Page,
    locator: Locator,
    *,
    telemetry: TimelineRecorder | None = None,
    timeout_ms: int = DEFAULT_DISPATCH_TIMEOUT_MS,
) -> None:
    """點擊後補送事件。

    加減號按鈕綁 `ng-click`，點擊本身足夠；但條款 checkbox 必須額外 dispatch
    才會更新 model（見 `selectors.py` 的 TERMS_CHECKBOX 註記）。統一補送較安全。

    **補送失敗不得中斷流程**：會導航的按鈕（配位、下一步、確認表單）一點下去
    元素就從 DOM 消失，此時 evaluate 必然失敗——那代表點擊已經生效，不是錯誤。
    但也不能無聲吞掉：記一筆 mark，讓「補送沒做到」在研究資料裡看得見。
    """
    await locator.click()
    try:
        await ng_dispatch(locator, timeout_ms=timeout_ms)
    except Exception as exc:
        if telemetry is not None:
            telemetry.record(
                TimelineEventType.MARK,
                DISPATCH_SKIPPED_MARK,
                reason=type(exc).__name__,
            )


async def ng_fill(
    page: Page,
    locator: Locator,
    value: str,
    *,
    timeout_ms: int = DEFAULT_DISPATCH_TIMEOUT_MS,
) -> None:
    """填值後補送事件。"""
    await locator.fill(value)
    await ng_dispatch(locator, timeout_ms=timeout_ms)


async def read_input_value(locator: Locator) -> str:
    return str(await locator.input_value())


async def page_text(page: Page) -> str:
    return str(await page.content())


def contains_cloudflare_challenge(text: str) -> str | None:
    """命中回傳該挑戰字串，否則 None。**只偵測，不繞過。**"""
    lowered = text.lower()
    for marker in KKTIXSelectors.CLOUDFLARE_CHALLENGE_TEXTS:
        if marker.lower() in lowered:
            return marker
    return None
