from __future__ import annotations

import asyncio
import contextlib
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Query, Request

from adapters.ticketing.base import ResolveError
from adapters.ticketing.factory import (
    UnsupportedPlatformError,
    build_resolver,
    detect_platform,
)
from adapters.ticketing.kktix.resolver import (
    MAX_ORGS_PER_SEARCH,
    KKTIXEventResolver,
    match_score,
)
from adapters.ticketing.kktix.selectors import KKTIX_EVENT_URL_RE
from api.deps import get_db, get_resolver, get_settings
from api.queries import count_unfinished_jobs, get_event_status_rows
from api.errors import (
    InvalidRequestError,
    NotFoundError,
    UnsupportedError,
    UpstreamFailedError,
)
from api.schemas.events import (
    EventCandidateOut,
    EventOut,
    EventSearchResponse,
    EventSearchResultOut,
    EventStatusesResponse,
    EventStatusOut,
    ResolveEventRequest,
    ResolveEventResponse,
    TicketingProviderOut,
    TicketTypeOut,
)
from api.settings import ApiSettings
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobState
from domain.event import Event, EventCandidate, EventStatus, PlatformEnum
from storage.database import Database
from storage.repositories.event_repository import EventRepository
from telemetry.logging import get_logger

router = APIRouter(prefix="/api/v1/events", tags=["events"])
logger = get_logger("api.events")

PROVIDER_NAMES = {"kktix": "KKTIX", "tixcraft": "拓元售票", "ibon": "ibon 售票"}
SEARCH_RESULT_LIMIT = 30
#: 背景補詳情時同時打上游的數量上限。
SEARCH_HYDRATE_CONCURRENCY = 4
#: 一次最多問幾場活動的狀態；搜尋一頁也就這個量級。
STATUS_QUERY_LIMIT = 60
#: 已經排過瀏覽器補資料的活動與排定的時刻；避免每次搜尋都重排同一批 job。
_hydration_requested: dict[str, float] = {}
#: 售票狀態會變（開賣、售完），排過一次就永不再排等於把第一次的結果當永久答案。
#: 隔一段時間讓同一場活動能重新補一次。
HYDRATION_TTL_S = 600.0
DESCRIPTION_MAX_CHARS = 300

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _provider_name(platform: str) -> str:
    return PROVIDER_NAMES.get(platform, platform.upper())


def _clean_description(raw: str | None) -> str | None:
    """票商 feed 的摘要夾帶 HTML；只留純文字，太長就截斷。"""
    if not raw:
        return None
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw)).strip()
    if not text:
        return None
    if len(text) > DESCRIPTION_MAX_CHARS:
        return text[: DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"
    return text


def _organizer_display(ev: Event) -> str | None:
    meta = ev.raw_metadata or {}
    display = meta.get("organizer_display")
    if (
        isinstance(display, str)
        and display.strip()
        and display.strip().lower() not in ("kktix", "tixcraft", "ibon")
    ):
        return display.strip()
    if ev.organizer and ev.organizer.lower() not in ("kktix", "tixcraft", "ibon"):
        return ev.organizer
    # 平台代號不是主辦單位。抓不到就誠實留空，讓前端顯示「未知」，
    # 不要用「拓元合作主辦單位」這種看起來有資料、其實是編的字串充數。
    return None


def _event_description(ev: Event) -> str | None:
    meta = ev.raw_metadata or {}
    for key in ("summary", "description"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_description(value)
    jsonld = meta.get("jsonld")
    if isinstance(jsonld, dict):
        value = jsonld.get("description")
        if isinstance(value, str) and value.strip():
            return _clean_description(value)
    return None


def _providers_for(ev: Event) -> list[TicketingProviderOut]:
    platform = ev.platform.value if hasattr(ev.platform, "value") else str(ev.platform)
    return [
        TicketingProviderOut(
            id=platform,
            name=_provider_name(platform),
            event_url=ev.canonical_url,
        )
    ]


def _is_detail_loaded(ev: Event) -> bool:
    """是否已經向平台要過詳情。

    早期版本用「有票種或有開賣時間」推論，但拓元的活動兩者都不在詳情頁上，
    於是永遠被判成沒補齊，前端就一直掛著「部分資料尚未取得」。改成由解析器
    在成功抓完詳情時自己標記，推論不到的欄位不再被當成補齊與否的證據。
    """
    meta = ev.raw_metadata or {}
    if meta.get("needs_browser_detail"):
        return False
    if meta.get("detail_source"):
        return True
    return bool(ev.ticket_types) or ev.sale_start_at is not None


def _event_to_out(ev: Event) -> EventOut:
    return EventOut(
        id=ev.id,
        platform=ev.platform.value
        if hasattr(ev.platform, "value")
        else str(ev.platform),
        organizer=ev.organizer,
        organizer_name=_organizer_display(ev),
        event_slug=ev.event_slug,
        title=ev.title,
        description=_event_description(ev),
        canonical_url=ev.canonical_url,
        ticketing_providers=_providers_for(ev),
        status=ev.status.value if hasattr(ev.status, "value") else str(ev.status),
        sale_start_at=ev.sale_start_at,
        sale_end_at=getattr(ev, "sale_end_at", None),
        event_start_at=ev.event_start_at,
        ticket_types=[
            TicketTypeOut(
                id=t.id,
                name=t.name,
                price=t.price,
                status=t.status.value if hasattr(t.status, "value") else str(t.status),
                remaining_count=getattr(
                    t, "remaining_count", getattr(t, "inventory_estimate", None)
                ),
                raw_id=t.raw_id,
            )
            for t in ev.ticket_types
        ],
        detail_loaded=_is_detail_loaded(ev),
        raw_metadata=dict(ev.raw_metadata or {}) if ev.raw_metadata else None,
    )


def _event_to_search_result(ev: Event) -> EventSearchResultOut:
    return EventSearchResultOut(
        id=ev.id,
        title=ev.title,
        description=_event_description(ev),
        organizer=_organizer_display(ev),
        ticketing_providers=_providers_for(ev),
        canonical_url=ev.canonical_url,
        sale_start_at=ev.sale_start_at,
        sale_end_at=getattr(ev, "sale_end_at", None),
        event_start_at=ev.event_start_at,
        status=ev.status.value if hasattr(ev.status, "value") else str(ev.status),
        detail_loaded=_is_detail_loaded(ev),
    )


def _candidate_to_event(candidate: EventCandidate) -> Event | None:
    """把 feed 候選轉成淺 Event；活動識別碼由平台的組織與代稱決定，因此穩定。"""
    try:
        platform = detect_platform(candidate.url.strip())
    except UnsupportedPlatformError:
        return None

    if platform == PlatformEnum.KKTIX:
        match = KKTIX_EVENT_URL_RE.match(candidate.url.strip())
        if match is None:
            return None
        org, slug = match.group("org"), match.group("slug")
        canonical_url = f"https://{org}.kktix.cc/events/{slug}"
    elif platform == PlatformEnum.TIXCRAFT:
        org = candidate.organizer or "tixcraft"
        slug = urlsplit(candidate.url.strip()).path.split("/")[-1]
        canonical_url = candidate.url.strip()
    elif platform == PlatformEnum.IBON:
        org = candidate.organizer or "ibon"
        slug = urlsplit(candidate.url.strip()).path.split("/")[-1]
        canonical_url = candidate.url.strip()
    else:
        return None

    raw_metadata: dict[str, Any] = {}
    summary = _clean_description(candidate.summary)
    if summary:
        raw_metadata["summary"] = summary
    if candidate.organizer and candidate.organizer.lower() not in (
        "kktix",
        "tixcraft",
        "ibon",
    ):
        raw_metadata["organizer_display"] = candidate.organizer

    # 搜尋層拿得到的欄位就地帶進來，別讓卡片為了場地與日期再去打一次詳情。
    extras = candidate.raw or {}
    for key in ("venue", "date_text", "image_url"):
        value = extras.get(key)
        if isinstance(value, str) and value.strip():
            raw_metadata[key] = value.strip()
    if extras.get("needs_browser_detail"):
        raw_metadata["needs_browser_detail"] = True

    status = EventStatus.UNKNOWN
    raw_status = extras.get("status")
    if isinstance(raw_status, str):
        with contextlib.suppress(ValueError):
            status = EventStatus(raw_status)

    return Event(
        id=Event.make_id(platform, org, slug),
        platform=platform,
        organizer=org,
        event_slug=slug,
        title=candidate.title,
        canonical_url=canonical_url,
        status=status,
        event_start_at=_iso_to_datetime(extras.get("event_start_at"))
        or candidate.published,
        sale_end_at=_iso_to_datetime(extras.get("sale_end_at")),
        raw_metadata=raw_metadata,
    )


def _iso_to_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _keep_stored_detail(ev: Event, stored: Event | None) -> Event:
    """別讓搜尋層的淺資料蓋掉已經補齊的詳情。

    upsert 是整欄覆寫 `raw_metadata`，所以同一場活動只要再被搜尋一次，稍早由
    Worker 抓回來的活動說明與主辦單位就會被列表層的淺資料洗掉，畫面上看起來
    像是「描述又不見了」。
    """
    if stored is None or not _is_detail_loaded(stored):
        return ev
    if _is_detail_loaded(ev):
        return ev
    merged = {**(ev.raw_metadata or {}), **(stored.raw_metadata or {})}
    # 售票狀態也要一起留。拓元的狀態只有場次頁看得出來，搜尋層拿不到就回
    # `UNKNOWN`；不把已經抓回來的狀態接回去，每搜一次就把它洗成「狀態未確認」。
    ev_status = ev.status.value if hasattr(ev.status, "value") else str(ev.status)
    return ev.model_copy(
        update={
            "raw_metadata": merged,
            "status": stored.status
            if ev_status == EventStatus.UNKNOWN.value
            else ev.status,
            "sale_start_at": ev.sale_start_at or stored.sale_start_at,
            "sale_end_at": ev.sale_end_at or stored.sale_end_at,
            "event_start_at": ev.event_start_at or stored.event_start_at,
            "ticket_types": ev.ticket_types or stored.ticket_types,
        }
    )


def _search_scope(
    known: list[Event], settings: ApiSettings, query: str
) -> list[str]:
    """搜尋範圍完全由後端推導：設定的主辦來源 ＋ 已同步活動的主辦。"""
    scope: list[str] = []
    for org in settings.resolver_orgs:
        if org and org not in scope:
            scope.append(org)
    for ev in known:
        if ev.organizer and ev.organizer not in scope:
            scope.append(ev.organizer)
    direct = KKTIX_EVENT_URL_RE.match(query.strip())
    if direct is not None and direct.group("org") not in scope:
        scope.insert(0, direct.group("org"))
    return scope[:MAX_ORGS_PER_SEARCH]


def _match_known_events(known: list[Event], query: str, limit: int) -> list[Event]:
    """已同步活動的標題比對；找不到上游時它就是唯一還答得出東西的來源。"""
    needle = query.strip().casefold()
    matched = [ev for ev in known if needle in ev.title.casefold()]
    matched.sort(key=lambda ev: (-match_score(query, ev.title), ev.title))
    return matched[:limit]


@router.get("/search", response_model=EventSearchResponse)
async def search_events(
    request: Request,
    q: str = Query(min_length=1),
    platform: str | None = Query(default=None),
    limit: int = Query(default=SEARCH_RESULT_LIMIT, ge=1, le=100),
    db: Database = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
    default_resolver: KKTIXEventResolver = Depends(get_resolver),
) -> EventSearchResponse:
    """依關鍵字或活動網址搜尋活動。支援單一平台篩選或三平台並行搜尋。"""
    query = q.strip()
    if not query:
        raise InvalidRequestError("query must not be empty")

    client = getattr(request.app.state, "http_client", None)

    # 1. 網址查詢：直接依網址判讀平台並抓取該場活動
    if query.startswith(("http://", "https://")):
        try:
            url_platform = detect_platform(query)
        except UnsupportedPlatformError as exc:
            raise InvalidRequestError(str(exc)) from exc
        resolver = build_resolver(url_platform, client=client)
        try:
            event = await resolver.fetch_event_metadata(query)
        except ResolveError as exc:
            raise UpstreamFailedError(str(exc)) from exc
        except Exception as exc:
            raise UpstreamFailedError(str(exc)) from exc
        try:
            async with db.session() as session:
                event = await EventRepository(session).upsert_event(event)
        except Exception as exc:
            logger.warning("direct_event_upsert_failed", error=str(exc))
        status_val = (
            event.status.value
            if hasattr(event.status, "value")
            else str(event.status)
        )
        results = (
            [_event_to_search_result(event)]
            if status_val != EventStatus.CLOSED.value
            else []
        )
        return EventSearchResponse(query=query, results=results)

    # 2. 本地已同步活動
    known: list[Event] = []
    async with db.session() as session:
        repo = EventRepository(session)
        if platform:
            try:
                p_enum = PlatformEnum(platform.lower())
                known = await repo.list_by_platform(p_enum)
            except ValueError as exc:
                raise UnsupportedError(f"Platform {platform} is not supported") from exc
        else:
            for p_enum in (PlatformEnum.KKTIX, PlatformEnum.TIXCRAFT, PlatformEnum.IBON):
                known.extend(await repo.list_by_platform(p_enum))

    local = _match_known_events(known, query, limit)
    scope = _search_scope(known, settings, query)

    # 3. 搜尋上游平台
    search_coros = []
    target_platforms: list[PlatformEnum] = []
    if platform:
        target_platforms = [PlatformEnum(platform.lower())]
    else:
        target_platforms = [PlatformEnum.KKTIX, PlatformEnum.TIXCRAFT, PlatformEnum.IBON]

    for p in target_platforms:
        res = build_resolver(p, client=client)
        if p == PlatformEnum.KKTIX:
            kktix_res = KKTIXEventResolver(client, orgs=scope) if isinstance(client, httpx.AsyncClient) else default_resolver
            if scope:
                search_coros.append(kktix_res.search(query, orgs=scope, limit=limit))
            search_coros.append(kktix_res.search_global(query, limit=limit))
        else:
            search_coros.append(res.search(query, limit=limit))

    search_results = await asyncio.gather(*search_coros, return_exceptions=True)
    remote: list[EventCandidate] = []
    scope_errors: list[Exception] = []
    for r in search_results:
        if isinstance(r, list):
            remote.extend(r)
        elif isinstance(r, Exception):
            scope_errors.append(r)

    # 若全數失敗且無本地結果，記錄警告並回傳空結果，避免 502 中斷前端 UI
    if not remote and not local and scope_errors and len(scope_errors) == len(search_coros):
        first_err = scope_errors[0]
        logger.warning("all_upstream_search_failed", query=query, error=str(first_err))
        return EventSearchResponse(query=query, results=[])

    scored: dict[str, tuple[float, Event]] = {}
    for ev in local:
        scored[ev.id] = (match_score(query, ev.title), ev)

    for candidate in remote:
        shallow = _candidate_to_event(candidate)
        if shallow is None:
            continue
        existing = scored.get(shallow.id)
        if existing is None:
            scored[shallow.id] = (candidate.score, shallow)
        elif candidate.score > existing[0]:
            scored[shallow.id] = (candidate.score, existing[1])

    ranked = sorted(scored.values(), key=lambda pair: (-pair[0], pair[1].title))
    top_events = [ev for _, ev in ranked[:limit]]

    # 已經查過的活動把詳情與狀態接回來，畫面才不會每搜一次就退回「狀態未確認」。
    known_by_id = {ev.id: ev for ev in known}
    top_events = [_keep_stored_detail(ev, known_by_id.get(ev.id)) for ev in top_events]

    if top_events:
        try:
            async with db.session() as session:
                repo = EventRepository(session)
                for ev in top_events:
                    await repo.upsert_event(ev)
        except Exception as exc:
            logger.warning("searched_event_upsert_failed", error=str(exc))

    active_events = [
        ev
        for ev in top_events
        if (ev.status.value if hasattr(ev.status, "value") else str(ev.status))
        != EventStatus.CLOSED.value
    ]

    # 狀態不在這裡算完。搜尋只負責把活動交出去，狀態交給兩條非同步的路去補，
    # 前端再用 /statuses 把結果接回卡片上：
    #
    #   1. 純 HTTP 問得到的部分（KKTIX／ibon 的尚未開賣、販售中、已結束）由這個
    #      行程自己在背景問，沒有 Worker 也能有狀態。
    #   2. 要瀏覽器才看得到的部分（拓元的場次表、KKTIX／ibon 的售完）排一張 job
    #      給 Worker，一整批一起處理。
    # 整頁結果都要補，不是只補前幾筆——補不到的那些會一路掛著「狀態未確認」，
    # 使用者看到的就是一整片沒有答案的卡片。
    _spawn(_hydrate_over_http(active_events, db=db, client=client))

    broker = getattr(request.app.state, "broker", None)
    if broker is not None:
        await _queue_browser_hydration(
            active_events, broker=broker, settings=settings
        )

    return EventSearchResponse(
        query=query,
        results=[_event_to_search_result(ev) for ev in active_events],
    )


#: 背景工作要留著參考，否則事件迴圈可能在跑完之前就把 task 回收掉。
_background_tasks: set[asyncio.Task[None]] = set()


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def drain_background_tasks(timeout: float = 30.0) -> None:
    """等背景補資料跑完。給測試用，讓「非同步補齊」這件事可以被斷言。"""
    while _background_tasks:
        pending = list(_background_tasks)
        done, _ = await asyncio.wait(pending, timeout=timeout)
        if not done:
            return


def reset_hydration_bookkeeping() -> None:
    """清掉「已經排過補資料」的紀錄。測試之間必須互不影響。"""
    _hydration_requested.clear()


def cancel_background_tasks() -> None:
    """關閉服務時把還在跑的背景工作收掉，別讓它們對著已經關掉的資料庫寫入。"""
    for task in list(_background_tasks):
        task.cancel()
    _background_tasks.clear()


async def _hydrate_over_http(
    events: list[Event], *, db: Database, client: Any
) -> None:
    """搜尋回應送出之後，才去問純 HTTP 問得到的活動詳情。

    這一段不含瀏覽器（G32），所以拿不到拓元的場次表——那個交給 Worker。KKTIX 與
    ibon 則**到此為止就是答案**：狀態只分尚未開賣與販售中，兩者純 HTTP 都問得到，
    不需要再進購票頁，所以這裡直接標記成已確認，前端不必再等 Worker。

    放在背景是因為它會逐一打上游，擺在請求裡會讓搜尋卡上好幾秒——使用者要的是
    先看到活動，狀態晚一點到沒關係。
    """
    gate = asyncio.Semaphore(SEARCH_HYDRATE_CONCURRENCY)

    async def one(ev: Event) -> Event | None:
        if (ev.raw_metadata or {}).get("needs_browser_detail"):
            return None
        try:
            async with gate:
                resolver = build_resolver(ev.platform, client=client)
                hydrated = await resolver.fetch_event_metadata(ev.canonical_url)
        except Exception:
            return None
        merged = {
            **(ev.raw_metadata or {}),
            **(hydrated.raw_metadata or {}),
            "availability_checked_at": datetime.now(UTC).isoformat(),
        }
        return hydrated.model_copy(update={"raw_metadata": merged})

    hydrated = [ev for ev in await asyncio.gather(*(one(ev) for ev in events)) if ev]
    if not hydrated:
        return
    try:
        async with db.session() as session:
            repo = EventRepository(session)
            for ev in hydrated:
                await repo.upsert_event(ev)
    except Exception as exc:  # noqa: BLE001 — 背景補資料失敗不該影響任何請求
        logger.warning("background_hydrate_upsert_failed", error=str(exc))


@router.get("/statuses", response_model=EventStatusesResponse)
async def get_event_statuses(
    ids: str = Query(min_length=1, description="以逗號分隔的活動 id"),
    db: Database = Depends(get_db),
) -> EventStatusesResponse:
    """批次查售票狀態，讓搜尋結果的卡片可以逐筆把狀態補上。

    只讀資料庫、不打上游：寫入的是背景工作與 Worker，這裡負責把結果交出去，
    順便說一句「還有沒有人在補」——前端要靠它決定還要不要等。
    """
    wanted = [part.strip() for part in ids.split(",") if part.strip()]
    if not wanted:
        raise InvalidRequestError("ids must not be empty")
    wanted = wanted[:STATUS_QUERY_LIMIT]

    rows = await get_event_status_rows(db, wanted)
    return EventStatusesResponse(
        results=[_row_to_status(row) for row in rows],
        pending=await _hydration_in_progress(db),
    )


async def _hydration_in_progress(db: Database) -> bool:
    """還有沒有人在補票況。

    兩個來源都要算：這個行程自己的背景工作（KKTIX／ibon 的純 HTTP 那段），以及
    broker 上還沒跑完的瀏覽器 job（拓元）。兩段之間有空檔，只看其中一邊會讓前端
    在中場休息時就以為結束了。
    """
    if _background_tasks:
        return True
    with contextlib.suppress(Exception):
        return await count_unfinished_jobs(db, JobKind.EVENT_HYDRATE.value) > 0
    return False


def _row_to_status(row: dict[str, Any]) -> EventStatusOut:
    metadata = row.get("raw_metadata") or {}
    return EventStatusOut(
        id=row["id"],
        status=row["status"],
        sale_start_at=row.get("sale_start_at"),
        sale_end_at=row.get("sale_end_at"),
        event_start_at=row.get("event_start_at"),
        detail_loaded=_metadata_detail_loaded(metadata),
        checked_at=_iso_to_datetime(metadata.get("availability_checked_at")),
    )


def _metadata_detail_loaded(metadata: dict[str, Any]) -> bool:
    if metadata.get("needs_browser_detail"):
        return False
    return bool(metadata.get("detail_source"))


async def _queue_browser_hydration(
    events: list[Event], *, broker: SqliteTaskBroker, settings: ApiSettings
) -> None:
    """把要開瀏覽器的活動排成**一張** job 交給 Worker。

    一場一張 job 的話，Worker 對同一個 profile 是一次領一張，十幾場就排成十幾輪；
    合成一張之後 Worker 可以用同一個瀏覽器同時開幾個分頁跑完。

    只有拓元要排。它的狀態只有場次頁看得出來，而那一頁擋純 HTTP；KKTIX 與 ibon
    的狀態上面那段背景工作就問完了，不必再讓 Worker 跑一次。
    """
    now = time.monotonic()
    rows: list[dict[str, Any]] = []
    for ev in events:
        platform = (
            ev.platform.value if hasattr(ev.platform, "value") else str(ev.platform)
        )
        if platform != PlatformEnum.TIXCRAFT.value:
            continue
        queued_at = _hydration_requested.get(ev.id)
        if queued_at is not None and now - queued_at < HYDRATION_TTL_S:
            continue
        _hydration_requested[ev.id] = now
        rows.append(
            {
                "platform": platform,
                "slug": ev.event_slug,
                "event_id": ev.id,
                "canonical_url": ev.canonical_url,
            }
        )

    if not rows:
        return
    try:
        await broker.enqueue(
            kind=JobKind.EVENT_HYDRATE,
            profile=settings.default_profile,
            payload={"events": rows},
        )
    except Exception as exc:  # noqa: BLE001 — 補資料失敗不該讓搜尋失敗
        for row in rows:
            _hydration_requested.pop(row["event_id"], None)
        logger.warning("event_hydrate_enqueue_failed", count=len(rows), error=str(exc))


@router.post("/resolve", response_model=ResolveEventResponse)
async def resolve_event(
    req: ResolveEventRequest,
    request: Request,
    db: Database = Depends(get_db),
    default_resolver: KKTIXEventResolver = Depends(get_resolver),
) -> ResolveEventResponse:
    client = getattr(request.app.state, "http_client", None)
    if req.query.startswith(("http://", "https://")):
        try:
            url_platform = detect_platform(req.query)
            resolver = build_resolver(url_platform, client=client)
        except UnsupportedPlatformError:
            resolver = default_resolver
    elif req.orgs is not None:
        resolver = KKTIXEventResolver(client, orgs=req.orgs) if isinstance(client, httpx.AsyncClient) else default_resolver
    else:
        resolver = default_resolver

    try:
        res = await resolver.resolve(req.query)
    except ResolveError as exc:
        if "organizer feed scope required" in str(exc).lower():
            raise InvalidRequestError(str(exc)) from exc
        raise UpstreamFailedError(str(exc)) from exc
    except Exception as exc:
        raise UpstreamFailedError(str(exc)) from exc

    if req.persist and res.event is not None:
        async with db.session() as session:
            repo = EventRepository(session)
            await repo.upsert_event(res.event)

    candidates = [
        EventCandidateOut(
            title=c.title,
            url=c.url,
            score=c.score,
            matched_by=getattr(c, "matched_by", "search"),
        )
        for c in res.candidates
    ]

    return ResolveEventResponse(
        auto_selected=res.auto_selected,
        event=_event_to_out(res.event) if res.event is not None else None,
        candidates=candidates,
    )


#: 瀏覽器補資料要開頁面、等 JS 驗證跑完，30 秒是「使用者還願意等」的上限。
BROWSER_HYDRATE_TIMEOUT_S = 30.0
BROWSER_HYDRATE_POLL_S = 0.5


async def _hydrate_via_worker(
    ev: Event, *, broker: SqliteTaskBroker, db: Database, settings: ApiSettings
) -> Event:
    """把需要瀏覽器的詳情抓取外包給 Worker，等它寫回資料庫後再讀一次。

    API Server 本身不得持有瀏覽器（G32），但「拿不到活動說明」對使用者來說
    就是缺資料，所以這裡等一段有上限的時間，而不是讓前端拿到半套資料就算了。
    """
    job_id = await broker.enqueue(
        kind=JobKind.EVENT_HYDRATE,
        profile=settings.default_profile,
        payload={
            "platform": ev.platform.value,
            "slug": ev.event_slug,
            "event_id": ev.id,
            "canonical_url": ev.canonical_url,
        },
    )

    deadline = asyncio.get_running_loop().time() + BROWSER_HYDRATE_TIMEOUT_S
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(BROWSER_HYDRATE_POLL_S)
        job = await broker.get(job_id)
        if job is None:
            break
        if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
            break
    else:
        logger.warning("event_hydrate_timeout", event_id=ev.id)

    async with db.session() as session:
        refreshed = await EventRepository(session).get_by_id(ev.id)
    return refreshed or ev


@router.get("/{event_id}", response_model=EventOut)
async def get_event(
    event_id: str,
    request: Request,
    db: Database = Depends(get_db),
    settings: ApiSettings = Depends(get_settings),
) -> EventOut:
    async with db.session() as session:
        ev = await EventRepository(session).get_by_id(event_id)
    if ev is None:
        raise NotFoundError(f"Event {event_id} not found")

    if _is_detail_loaded(ev):
        return _event_to_out(ev)

    # 先走純 HTTP 的解析器；ibon 三支 JSON API 這一步就補齊了。
    client = getattr(request.app.state, "http_client", None)
    resolver = build_resolver(ev.platform, client=client)
    try:
        hydrated = await resolver.fetch_event_metadata(ev.canonical_url)
    except Exception as exc:
        logger.warning("event_hydrate_failed", event_id=ev.id, error=str(exc))
        hydrated = None

    if hydrated is not None:
        merged_metadata = {**(ev.raw_metadata or {}), **(hydrated.raw_metadata or {})}
        hydrated = hydrated.model_copy(update={"raw_metadata": merged_metadata})
        async with db.session() as session:
            ev = await EventRepository(session).upsert_event(hydrated)

    # 拓元的節目介紹頁擋純 HTTP，只有 Worker 的瀏覽器打得開。
    broker = getattr(request.app.state, "broker", None)
    if broker is not None and (ev.raw_metadata or {}).get("needs_browser_detail"):
        try:
            ev = await _hydrate_via_worker(
                ev, broker=broker, db=db, settings=settings
            )
        except Exception as exc:
            logger.warning("event_browser_hydrate_failed", event_id=ev.id, error=str(exc))

    return _event_to_out(ev)


@router.get("", response_model=list[EventOut])
async def list_events(
    platform: str = "kktix",
    db: Database = Depends(get_db),
) -> list[EventOut]:
    try:
        plat_enum = PlatformEnum(platform.lower())
    except ValueError as exc:
        raise UnsupportedError(f"Platform {platform} is not supported") from exc

    async with db.session() as session:
        repo = EventRepository(session)
        events = await repo.list_by_platform(plat_enum)
        return [_event_to_out(ev) for ev in events]
