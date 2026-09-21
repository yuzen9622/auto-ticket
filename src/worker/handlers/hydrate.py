"""活動詳情補齊：用瀏覽器打開票務平台擋掉純 HTTP 的頁面，把結果寫回活動表。

只有拓元需要走這條路——它的節目介紹頁與場次頁一律先回 401 的 JS 驗證頁，httpx
拿到的永遠是驗證頁而不是活動內容。API Server 依約不得碰瀏覽器（G32），所以改由
它送一張 job 過來，由持有瀏覽器的 Worker 代打。
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any

from adapters.ticketing.tixcraft.pages import (
    detail_url,
    game_url,
    parse_activity_detail,
)
from adapters.ticketing.tixcraft.resolver import (
    TixcraftEventResolver,
    build_event,
)
from broker.broker import SqliteTaskBroker
from broker.jobs import JobRecord
from broker.outbox import OutboxWriter
from browser.context_factory import BrowserProfile, build_persistent_context_options
from browser.system_chrome import SystemChromeError, ensure_system_chrome
from storage.database import Database
from storage.repositories.event_repository import EventRepository

from ..settings import WorkerSettings

logger = logging.getLogger(__name__)

#: 驗證頁的特徵字串；出現代表這次拿到的不是活動內容。
CHALLENGE_MARKERS = (
    "Let's Get Your Identity Verified",
    "Your Browsing Activity Has Been Paused",
    '{"response":"identify"}',
)

PAGE_SETTLE_MS = 6000
HYDRATE_PROFILE_NAME = "hydrate"


def _is_challenge(html: str) -> bool:
    return any(marker in html for marker in CHALLENGE_MARKERS)


async def _render_with_persistent_chromium(urls: list[str]) -> dict[str, str]:
    """先用自帶的無頭瀏覽器試一次：靜悄悄、不會跳出使用者的視窗。

    沿用固定的 user-data-dir，驗證通過後拿到的 cookie 下次還在，不必每次重解。
    """
    from playwright.async_api import async_playwright

    profile = BrowserProfile(
        name=HYDRATE_PROFILE_NAME,
        user_data_dir=Path(".browser_profiles") / HYDRATE_PROFILE_NAME,
        headless=True,
    )
    options = build_persistent_context_options(profile)
    results: dict[str, str] = {}
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile.user_data_dir), **options
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            for url in urls:
                with contextlib.suppress(Exception):
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(PAGE_SETTLE_MS)
                    results[url] = await page.content()
        finally:
            with contextlib.suppress(Exception):
                await context.close()
    return results


async def _render_with_system_chrome(
    urls: list[str], *, settings: WorkerSettings
) -> dict[str, str]:
    """自帶瀏覽器被擋下時的退路：借使用者本機那顆 Chrome。"""
    from playwright.async_api import async_playwright

    endpoint = settings.cdp_endpoint
    if endpoint is None:
        if not settings.auto_launch_browser:
            raise SystemChromeError("未啟用自動開啟瀏覽器，且沒有設定 CDP 端點")
        launched = await ensure_system_chrome(
            port=settings.browser_debug_port, initial_url=urls[0]
        )
        endpoint = launched.endpoint

    results: dict[str, str] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else None
        if context is None:
            raise SystemChromeError("借用的瀏覽器沒有可用的 context")
        page = await context.new_page()
        try:
            for url in urls:
                with contextlib.suppress(Exception):
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(PAGE_SETTLE_MS)
                    results[url] = await page.content()
        finally:
            with contextlib.suppress(Exception):
                await page.close()
    return results


async def render_tixcraft_pages(
    slug: str, *, settings: WorkerSettings
) -> dict[str, str]:
    """回傳節目介紹頁與場次頁的完整 HTML；兩種瀏覽器都過不了就回空字典。"""
    urls = [detail_url(slug), game_url(slug)]
    try:
        rendered = await _render_with_persistent_chromium(urls)
    except Exception as exc:
        logger.warning("hydrate_headless_render_failed: %s (%s)", slug, exc)
        rendered = {}

    detail_html = rendered.get(urls[0], "")
    if detail_html and not _is_challenge(detail_html):
        return rendered

    logger.info("hydrate_escalating_to_system_chrome: %s", slug)
    try:
        return await _render_with_system_chrome(urls, settings=settings)
    except Exception as exc:
        logger.warning("hydrate_system_chrome_failed: %s (%s)", slug, exc)
        return {}


async def execute_event_hydrate(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    payload: dict[str, Any] = dict(job.payload or {})
    platform = str(payload.get("platform") or "").lower()
    slug = str(payload.get("slug") or "")

    if platform != "tixcraft" or not slug:
        await broker.complete(
            job.id,
            worker_id=worker_id,
            result={"hydrated": False, "reason": "unsupported_platform"},
        )
        return

    rendered = await render_tixcraft_pages(slug, settings=settings)
    detail_html = rendered.get(detail_url(slug), "")
    if not detail_html or _is_challenge(detail_html):
        await broker.complete(
            job.id,
            worker_id=worker_id,
            result={"hydrated": False, "reason": "challenge_not_cleared"},
        )
        return

    detail = parse_activity_detail(
        detail_html, game_html=rendered.get(game_url(slug)) or None
    )
    resolver = TixcraftEventResolver()
    listing = await resolver.listing_for(slug)
    event = build_event(slug, listing=listing, detail=detail)

    async with db.session() as session:
        await EventRepository(session).upsert_event(event)

    await broker.complete(
        job.id,
        worker_id=worker_id,
        result={
            "hydrated": True,
            "event_id": event.id,
            "has_description": bool(detail.description),
            "sessions": len(detail.sessions),
        },
    )
