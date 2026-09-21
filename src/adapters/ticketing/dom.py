"""平台中立的 DOM 操作與輔助函式。

提供所有票券平台 adapter 共用的 DOM 操作、候選選擇器探測、
事件補送，以及 Cloudflare / Turnstile 單次探測。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page
else:
    Locator = Any
    Page = Any

DEFAULT_PROBE_TIMEOUT_MS = 2000
DEFAULT_OPTIONAL_PROBE_MS = 500
DEFAULT_DISPATCH_TIMEOUT_MS = 1000
#: 零等待探測時最多檢查幾個同選擇器的元素；再多就是頁面結構有問題。
PRESENT_SCAN_LIMIT = 10
SELECTOR_FALLBACK_MARK = "selector_fallback"
DISPATCH_SKIPPED_MARK = "ng_dispatch_skipped"

DEFAULT_CLOUDFLARE_CHALLENGE_TEXTS: tuple[str, ...] = (
    "正在執行安全驗證",
    "just a moment",
    "請啟用 javascript 與 cookie 以繼續",
    "驗證您是人類",
)

_NG_DISPATCH = (
    "el => {"
    " el.dispatchEvent(new Event('input', { bubbles: true }));"
    " el.dispatchEvent(new Event('change', { bubbles: true }));"
    "}"
)


def candidate_selectors(selectors: str | Sequence[str]) -> tuple[str, ...]:
    """把選擇器正規化成候選清單，保持宣告順序。"""
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
    """依候選順序回傳第一個可見元素；全數落空回 `None`（不拋例外）。"""
    candidates = candidate_selectors(selectors)
    if not candidates:
        return None
    per_try = max(1, timeout_ms // len(candidates))
    for rank, selector in enumerate(candidates, start=1):
        locator = page.locator(selector).first
        visible = False
        with contextlib.suppress(Exception):
            await locator.wait_for(state="visible", timeout=per_try)
            visible = True
        if not visible:
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


async def first_present_visible(
    page: Page, selectors: str | Sequence[str]
) -> Locator | None:
    """**現在**就可見的第一個元素；元素不在 DOM 裡就立刻回 None。

    和 `first_visible` 只差一件事：它不等。`first_visible` 的語意是「等這個元素
    出現」，用在開賣瞬間才長出來的按鈕上是對的；但「現在有沒有售罄彈窗」「這一頁
    有沒有驗證碼」是**問句**不是等待——拿 `first_visible` 去問，問一次就付一次
    逾時，跑在每輪判頁的熱迴圈上會把整個搶票時間吃光。
    """
    for selector in candidate_selectors(selectors):
        locator = page.locator(selector)
        try:
            total = await locator.count()
        except Exception:
            continue
        for index in range(min(total, PRESENT_SCAN_LIMIT)):
            candidate = locator.nth(index)
            with contextlib.suppress(Exception):
                if await candidate.is_visible():
                    return candidate
    return None


async def present_count(page: Page, selectors: str | Sequence[str]) -> int:
    """符合的元素在 DOM 裡有幾個，不看可見性、不等待。

    座位圖的 `<area>` 是零尺寸元素，Playwright 的可見性判定對它永遠是 False；
    這種頁面只能用「存不存在」判斷，用可見性問只會等到逾時再拿到錯的答案。
    """
    total = 0
    for selector in candidate_selectors(selectors):
        with contextlib.suppress(Exception):
            total += await page.locator(selector).count()
    return total


async def ng_dispatch(
    locator: Locator, *, timeout_ms: int = DEFAULT_DISPATCH_TIMEOUT_MS
) -> None:
    """補送 `input` / `change` 事件以驅動前端 framework（如 AngularJS / Vue）的 model 更新。"""
    await locator.evaluate(_NG_DISPATCH, timeout=timeout_ms)


async def ng_click(
    page: Page,
    locator: Locator,
    *,
    telemetry: TimelineRecorder | None = None,
    timeout_ms: int = DEFAULT_DISPATCH_TIMEOUT_MS,
) -> None:
    """點擊後補送事件。

    補送失敗不得中斷流程：會導航的按鈕一點下去元素就從 DOM 消失，
    此時 evaluate 必然失敗——那代表點擊已經生效，不是錯誤。
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


async def page_text(
    page: Page, *, settle_timeout_ms: int = DEFAULT_PROBE_TIMEOUT_MS
) -> str:
    """讀取頁面內容；導頁進行中先等 DOM 穩定再重試一次。"""
    try:
        return str(await page.content())
    except Exception:
        await page.wait_for_load_state("domcontentloaded", timeout=settle_timeout_ms)
        return str(await page.content())


def contains_cloudflare_challenge(
    text: str, markers: Sequence[str] | None = None
) -> str | None:
    """命中回傳該挑戰字串，否則 None。"""
    lowered = text.lower()
    check_markers = (
        markers if markers is not None else DEFAULT_CLOUDFLARE_CHALLENGE_TEXTS
    )
    for marker in check_markers:
        if marker.lower() in lowered:
            return marker
    return None


async def try_solve_cloudflare_turnstile(
    page: Page,
    *,
    is_challenge_active: Callable[[], Awaitable[bool]] | None = None,
    wait_ms: int = 500,
) -> bool:
    """單次探測並嘗試受控點擊 Cloudflare Turnstile。

    遵守單次契約：
    1. 先判斷 challenge 是否已自行消失；消失回 True。
    2. 探測 challenges.cloudflare.com iframe、.cf-turnstile 或 Turnstile checkbox。
    3. 若元素可見，透過 Playwright locator/frame 執行一次受控點擊；找不到可點元素時只做一次短等待。
    4. 再 probe 一次；challenge 消失回 True，仍存在回 False。
    5. 不在內部做無界重試。
    """
    if is_challenge_active is not None and not await is_challenge_active():
        return True

    # 嘗試尋找 Turnstile iframe / checkbox
    clicked = False
    turnstile_selectors = (
        "iframe[src*='challenges.cloudflare.com']",
        ".cf-turnstile iframe",
        "#cf-turnstile iframe",
    )

    for sel in turnstile_selectors:
        with contextlib.suppress(Exception):
            iframe_loc = page.locator(sel).first
            if await iframe_loc.is_visible():
                frame = page.frame_locator(sel)
                box = frame.locator("input[type='checkbox'], .cb-i, #challenge-stage, .ctp-checkbox-label").first
                if await box.is_visible():
                    await box.click(timeout=1000)
                    clicked = True
                    break

    if not clicked:
        direct_selectors = (
            ".cf-turnstile",
            "#turnstile-wrapper",
            "[data-sitekey]",
        )
        for sel in direct_selectors:
            with contextlib.suppress(Exception):
                loc = page.locator(sel).first
                if await loc.is_visible():
                    await loc.click(timeout=1000)
                    clicked = True
                    break

    # 短暫等待沉澱
    await asyncio.sleep(wait_ms / 1000.0)

    # 再次確認挑戰是否消失
    if is_challenge_active is not None:
        return not await is_challenge_active()

    # 預設透過 page_text 檢查
    text = await page_text(page)
    return contains_cloudflare_challenge(text) is None
