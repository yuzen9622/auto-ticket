"""活動狀態補齊：用瀏覽器打開票務平台擋掉純 HTTP 的頁面，把結果寫回活動表。

搜尋不等這件事。API Server 一拿到候選就把結果回給前端，再送一張 job 過來，由持有
瀏覽器的 Worker 慢慢把每一場的真實狀態補上（G32：API 依約不得碰瀏覽器）。一張 job
帶一整批活動，處理時彼此並行——逐一排隊會讓十幾筆結果等上好幾分鐘。

只有拓元需要瀏覽器：它的節目介紹頁與場次頁一律先回 401 的 JS 驗證頁，純 HTTP 永遠
拿不到內容。KKTIX 與 ibon 的狀態純 HTTP 就問得到。

售票狀態對外只分「尚未開賣／販售中」，所以這裡不做售完探測。KKTIX 的購票登記頁與
ibon 的訂購頁都會擋掉無頭瀏覽器（各自回 403），只有借使用者本機那顆 Chrome 才讀得到
——為了一個狀態在背景彈出瀏覽器視窗，而且每搜尋一次就彈一輪，完全不成比例。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from adapters.ticketing.factory import build_resolver
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
from browser.system_chrome import SystemChromeError, probe_debug_port
from domain.event import Event, PlatformEnum
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

#: 等頁面把要的元素掛上來的上限。正常是一秒內，等不到多半是被驗證頁擋住了。
READY_TIMEOUT_MS = 8000
HYDRATE_PROFILE_NAME = "hydrate"
#: 同時開幾個分頁。開太多會被平台當成攻擊，開太少整批就退化成排隊。
PAGE_CONCURRENCY = 4
#: 一張 job 最多處理幾場活動。
MAX_EVENTS_PER_JOB = 30
#: 拓元兩頁各自「抓到這個就可以走了」的元素。
INTRO_SELECTOR = "#intro"
GAME_LIST_SELECTOR = "#gameList"


def _is_challenge(html: str) -> bool:
    return any(marker in html for marker in CHALLENGE_MARKERS)


#: Playwright 沒下載瀏覽器時丟的錯長這樣。
BROWSER_MISSING_MARKERS = (
    "Executable doesn't exist",
    "playwright install",
)


def _browser_not_installed(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in BROWSER_MISSING_MARKERS)


@dataclass(frozen=True, slots=True)
class StatusRequest:
    """要補狀態的一場活動。"""

    event_id: str
    platform: str
    slug: str
    canonical_url: str


def parse_requests(payload: dict[str, Any]) -> list[StatusRequest]:
    """payload 支援批次 `events: [...]`，也收舊的單筆格式。"""
    raw = payload.get("events")
    rows = raw if isinstance(raw, list) else [payload]
    requests: list[StatusRequest] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or "").strip()
        platform = str(row.get("platform") or "").strip().lower()
        if not event_id or not platform or event_id in seen:
            continue
        seen.add(event_id)
        requests.append(
            StatusRequest(
                event_id=event_id,
                platform=platform,
                slug=str(row.get("slug") or "").strip(),
                canonical_url=str(row.get("canonical_url") or "").strip(),
            )
        )
    return requests[:MAX_EVENTS_PER_JOB]


# ---------------------------------------------------------------- 瀏覽器分頁池


class _PagePool:
    """一批活動共用一個瀏覽器，分頁數有上限。

    每場活動各自開一個瀏覽器的話，十幾場就是十幾次冷啟動；共用一個 context 還能
    共用已經解過的人機驗證 cookie。
    """

    def __init__(self, context: Any, *, size: int = PAGE_CONCURRENCY) -> None:
        self._context = context
        self._gate = asyncio.Semaphore(size)

    @contextlib.asynccontextmanager
    async def page(self) -> Any:
        async with self._gate:
            page = await self._context.new_page()
            try:
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await page.close()

    async def fetch(self, url: str, *, ready_selector: str | None = None) -> str:
        """抓一頁 HTML；等到要的東西出現就走，不死等固定秒數。

        原本每頁固定睡六秒，但實測拓元的 `#gameList` 在 0.9 秒就掛上了——十幾筆
        活動乘以兩頁，光是空等就把整批拖到半分鐘。等不到也照樣把 HTML 交出去：
        那通常是人機驗證頁，呼叫端要看內容才認得出來。
        """
        async with self.page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if ready_selector is not None:
                with contextlib.suppress(Exception):
                    await page.wait_for_selector(
                        ready_selector, state="attached", timeout=READY_TIMEOUT_MS
                    )
            return await page.content()


@contextlib.asynccontextmanager
async def _headless_pool() -> Any:
    """自帶的無頭瀏覽器：靜悄悄、不會跳出使用者的視窗。

    沿用固定的 user-data-dir，驗證通過後拿到的 cookie 下次還在，不必每次重解。
    """
    from playwright.async_api import async_playwright

    profile = BrowserProfile(
        name=HYDRATE_PROFILE_NAME,
        user_data_dir=Path(".browser_profiles") / HYDRATE_PROFILE_NAME,
        headless=True,
    )
    options = build_persistent_context_options(profile)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile.user_data_dir), **options
        )
        try:
            yield _PagePool(context)
        finally:
            with contextlib.suppress(Exception):
                await context.close()


@contextlib.asynccontextmanager
async def _system_chrome_pool(settings: WorkerSettings) -> Any:
    """自帶瀏覽器被擋下時的退路：借使用者本機那顆 Chrome。

    **只接已經開著的**，不會自己去開一顆。補票況是背景工作，為了它在使用者面前
    彈出一個瀏覽器視窗完全不成比例——尤其這件事每搜尋一次就會發生一輪。真的需要
    開視窗的是搶票與登入，那些流程自己會呼叫 `ensure_system_chrome`。

    使用者如果已經因為搶票或登入而開著那顆 Chrome（偵錯埠活著），這裡就順手借來
    用；沒開就放棄，讓那幾場活動的票況停在「未確認」。
    """
    from playwright.async_api import async_playwright

    endpoint = settings.cdp_endpoint
    if endpoint is None:
        candidate = f"http://127.0.0.1:{settings.browser_debug_port}"
        if not await probe_debug_port(candidate):
            raise SystemChromeError(
                "沒有已經開著的瀏覽器可借；補票況不會自己開一顆"
            )
        endpoint = candidate

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else None
        if context is None:
            raise SystemChromeError("借用的瀏覽器沒有可用的 context")
        yield _PagePool(context)


# ---------------------------------------------------------------- 各平台的補法


async def _resolve_tixcraft(
    request: StatusRequest, pool: _PagePool
) -> tuple[Event | None, bool]:
    """回傳（活動, 是否撞到人機驗證）。"""
    slug = request.slug
    if not slug:
        return (None, False)

    detail_html = await pool.fetch(detail_url(slug), ready_selector=INTRO_SELECTOR)
    if _is_challenge(detail_html):
        return (None, True)
    game_html = await pool.fetch(game_url(slug), ready_selector=GAME_LIST_SELECTOR)
    if _is_challenge(game_html):
        game_html = None

    detail = parse_activity_detail(detail_html, game_html=game_html)
    listing = await TixcraftEventResolver().listing_for(slug)
    return (build_event(slug, listing=listing, detail=detail), False)


async def _resolve_one(
    request: StatusRequest,
    *,
    pool: _PagePool,
    client: httpx.AsyncClient,
) -> tuple[Event | None, bool]:
    """回傳（補好的活動, 是否撞到人機驗證）。"""
    if request.platform == PlatformEnum.TIXCRAFT.value:
        return await _resolve_tixcraft(request, pool)

    # KKTIX 與 ibon 的基本狀態純 HTTP 就問得到，Worker 自己問，不依賴 API 先寫好。
    # KKTIX 與 ibon 的「尚未開賣／販售中」純 HTTP 就問得到，不必開瀏覽器。
    resolver = build_resolver(request.platform, client=client)
    return (await resolver.fetch_event_metadata(request.canonical_url), False)


def _mark_checked(event: Event) -> Event:
    """標記這場活動的狀態已經被確認過，前端才知道不必再等。"""
    metadata = dict(event.raw_metadata or {})
    metadata["availability_checked_at"] = datetime.now(UTC).isoformat()
    return event.model_copy(update={"raw_metadata": metadata})


async def resolve_statuses(
    requests: list[StatusRequest],
    *,
    db: Database,
    settings: WorkerSettings,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """把一整批活動的狀態補齊，回傳 `event_id -> 狀態` 供 job 結果記錄。"""
    if not requests:
        return {}

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    resolved: dict[str, Event] = {}
    challenged: list[StatusRequest] = []

    async def run(pool: _PagePool, batch: list[StatusRequest]) -> None:
        async def one(request: StatusRequest) -> None:
            try:
                event, hit_challenge = await _resolve_one(
                    request, pool=pool, client=http
                )
            except Exception as exc:  # noqa: BLE001 — 單一場失敗不該拖垮整批
                logger.warning(
                    "event_status_failed: %s (%s)", request.event_id, exc
                )
                return
            if hit_challenge:
                challenged.append(request)
                return
            if event is not None:
                resolved[request.event_id] = _mark_checked(event)

        await asyncio.gather(*(one(request) for request in batch))

    try:
        try:
            async with _headless_pool() as pool:
                await run(pool, requests)
        except Exception as exc:  # noqa: BLE001 — 換另一顆瀏覽器再試
            if _browser_not_installed(exc):
                # 這不是「這次抓失敗」，是這台機器根本沒有可用的無頭瀏覽器；
                # 講清楚要跑哪一行，否則只會看到一句語焉不詳的警告。
                logger.warning(
                    "event_status_needs_browser_install: 沒有安裝無頭瀏覽器，"
                    "票況補不到。請執行 `pnpm run setup:browsers`"
                    "（等同 `uv run playwright install chromium`）。原始錯誤：%s",
                    exc,
                )
            else:
                logger.warning("event_status_headless_failed: %s", exc)
            challenged = list(requests)

        if challenged:
            retry = list(challenged)
            challenged = []
            try:
                async with _system_chrome_pool(settings) as pool:
                    logger.info(
                        "event_status_borrowing_open_browser: %d", len(retry)
                    )
                    await run(pool, retry)
            except SystemChromeError as exc:
                # 沒有現成的瀏覽器可借就到此為止，不會為了補票況彈出視窗。
                logger.info("event_status_skipped_no_browser: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event_status_system_chrome_failed: %s", exc)
    finally:
        if owns_client:
            await http.aclose()

    if resolved:
        async with db.session() as session:
            repo = EventRepository(session)
            for event in resolved.values():
                with contextlib.suppress(Exception):
                    await repo.upsert_event(event)

    return {
        event_id: (
            event.status.value
            if hasattr(event.status, "value")
            else str(event.status)
        )
        for event_id, event in resolved.items()
    }


async def execute_event_hydrate(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    requests = parse_requests(dict(job.payload or {}))
    if not requests:
        await broker.complete(
            job.id,
            worker_id=worker_id,
            result={"hydrated": 0, "reason": "no_events"},
        )
        return

    statuses = await resolve_statuses(requests, db=db, settings=settings)
    await broker.complete(
        job.id,
        worker_id=worker_id,
        result={
            "hydrated": len(statuses),
            "requested": len(requests),
            "statuses": statuses,
        },
    )
