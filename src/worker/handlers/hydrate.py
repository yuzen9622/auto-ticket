"""活動狀態補齊：用瀏覽器打開票務平台擋掉純 HTTP 的頁面，把結果寫回活動表。

搜尋不等這件事。API Server 一拿到候選就把結果回給前端，再送一張 job 過來，由持有
瀏覽器的 Worker 慢慢把每一場的真實狀態補上（G32：API 依約不得碰瀏覽器）。一張 job
帶一整批活動，處理時彼此並行——逐一排隊會讓十幾筆結果等上好幾分鐘。

三個平台要開瀏覽器的理由各不相同：

* 拓元：節目介紹頁與場次頁一律先回 401 的 JS 驗證頁，純 HTTP 永遠拿不到內容。
* KKTIX：活動主頁看得出尚未開賣／結束販售，但**看不到售完**；售完只寫在購票登記頁，
  那一頁純 HTTP 回 403，而且是 AngularJS，不跑 JS 連票種列都沒有。
* ibon：JSON API 的 `SoldOut` 旗標全站沒人設，售完只能從訂購頁的座位圖讀。
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
from adapters.ticketing.ibon.pages import (
    parse_zone_availability,
    zones_are_sold_out,
)
from adapters.ticketing.kktix.pages import (
    parse_registration_tickets,
    registration_is_sold_out,
)
from adapters.ticketing.kktix.selectors import KKTIX_EVENT_URL_RE
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
from domain.event import Event, EventStatus, PlatformEnum
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
#: 售完探測只要等頁面把票種／座位圖渲染出來，不必等滿六秒。
AVAILABILITY_SETTLE_MS = 2500
HYDRATE_PROFILE_NAME = "hydrate"
#: 同時開幾個分頁。開太多會被平台當成攻擊，開太少整批就退化成排隊。
PAGE_CONCURRENCY = 4
#: 一場活動最多探幾個場次。場次多的活動（職棒整季）逐場開頁會開到天亮，而且
#: 只要有一場買得到就不是售完，早就可以收手。
MAX_SESSION_PROBES = 3
#: 一張 job 最多處理幾場活動。
MAX_EVENTS_PER_JOB = 30


def _is_challenge(html: str) -> bool:
    return any(marker in html for marker in CHALLENGE_MARKERS)


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

    async def fetch(self, url: str, *, settle_ms: int = AVAILABILITY_SETTLE_MS) -> str:
        async with self.page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(settle_ms)
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
    """自帶瀏覽器被擋下時的退路：借使用者本機那顆 Chrome。"""
    from playwright.async_api import async_playwright

    endpoint = settings.cdp_endpoint
    if endpoint is None:
        if not settings.auto_launch_browser:
            raise SystemChromeError("未啟用自動開啟瀏覽器，且沒有設定 CDP 端點")
        launched = await ensure_system_chrome(port=settings.browser_debug_port)
        endpoint = launched.endpoint

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

    detail_html = await pool.fetch(detail_url(slug), settle_ms=PAGE_SETTLE_MS)
    if _is_challenge(detail_html):
        return (None, True)
    game_html = await pool.fetch(game_url(slug), settle_ms=PAGE_SETTLE_MS)
    if _is_challenge(game_html):
        game_html = None

    detail = parse_activity_detail(detail_html, game_html=game_html)
    listing = await TixcraftEventResolver().listing_for(slug)
    return (build_event(slug, listing=listing, detail=detail), False)


def _kktix_registration_url(event: Event) -> str | None:
    match = KKTIX_EVENT_URL_RE.match(event.canonical_url)
    if match is None:
        return None
    return f"https://kktix.com/events/{match.group('slug')}/registrations/new"


async def _refine_kktix(event: Event, pool: _PagePool) -> Event:
    """進購票登記頁看票種還選不選得到。

    活動主頁看不到售完，還有一種活動連票種表都沒有（主頁只有一顆購票鈕），狀態會
    停在 `UNKNOWN`。登記頁兩件事都說得出來：有張數輸入框就是還買得到，全部沒有
    就是售完。
    """
    url = _kktix_registration_url(event)
    if url is None:
        return event
    tickets = parse_registration_tickets(await pool.fetch(url))
    sold_out = registration_is_sold_out(tickets)
    if sold_out is True:
        return event.model_copy(update={"status": EventStatus.SOLD_OUT})
    if sold_out is False and event.status is EventStatus.UNKNOWN:
        return event.model_copy(update={"status": EventStatus.ON_SALE})
    return event


async def _refine_ibon(event: Event, pool: _PagePool) -> Event:
    """逐一進買得到的場次的訂購頁看座位圖；只要一場還有票就不是售完。"""
    sessions = [
        session
        for session in (event.raw_metadata or {}).get("sessions") or []
        if isinstance(session, dict)
        and session.get("can_buy")
        and session.get("purchase_url")
    ][:MAX_SESSION_PROBES]
    if not sessions:
        return event

    verdicts: list[bool | None] = []
    for session in sessions:
        zones = parse_zone_availability(await pool.fetch(str(session["purchase_url"])))
        verdict = zones_are_sold_out(zones)
        if verdict is False:
            return event
        verdicts.append(verdict)

    if verdicts and all(verdict is True for verdict in verdicts):
        return event.model_copy(update={"status": EventStatus.SOLD_OUT})
    return event


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
    resolver = build_resolver(request.platform, client=client)
    event = await resolver.fetch_event_metadata(request.canonical_url)
    # 還沒開賣或已經結束的活動沒有「售完」可言，不必再開一次瀏覽器。狀態不明的
    # 活動則相反——它正是最需要進購票頁問清楚的那一種。
    if event.status not in (EventStatus.ON_SALE, EventStatus.UNKNOWN):
        return (event, False)

    if request.platform == PlatformEnum.KKTIX.value:
        return (await _refine_kktix(event, pool), False)
    if request.platform == PlatformEnum.IBON.value and (
        event.status is EventStatus.ON_SALE
    ):
        return (await _refine_ibon(event, pool), False)
    return (event, False)


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
            logger.warning("event_status_headless_failed: %s", exc)
            challenged = list(requests)

        if challenged:
            logger.info("event_status_escalating_to_system_chrome: %d", len(challenged))
            retry = list(challenged)
            challenged = []
            try:
                async with _system_chrome_pool(settings) as pool:
                    await run(pool, retry)
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
