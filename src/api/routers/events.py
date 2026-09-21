from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import datetime
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
#: 只補前幾筆的詳情，而且限制同時打上游的數量。
SEARCH_HYDRATE_LIMIT = 12
SEARCH_HYDRATE_CONCURRENCY = 4
#: 已經排過瀏覽器補資料的活動；避免每次搜尋都重排同一批 job。
_hydration_requested: set[str] = set()
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
    return ev.model_copy(
        update={
            "raw_metadata": merged,
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

    # 補詳情會逐一打上游，搜尋結果一長就變成三十個請求打同一個網域。只補使用者
    # 第一眼會看到的前幾筆，而且一次只允許少量併發。
    hydrate_gate = asyncio.Semaphore(SEARCH_HYDRATE_CONCURRENCY)

    async def _hydrate(ev: Event, *, allowed: bool) -> Event:
        if _is_detail_loaded(ev) or not allowed:
            return ev
        # 需要瀏覽器才打得開的平台，在搜尋階段再試一次 HTTP 也只是被同一道
        # 人機驗證擋掉；留給使用者點進活動時由 Worker 補。
        if (ev.raw_metadata or {}).get("needs_browser_detail"):
            return ev
        try:
            async with hydrate_gate:
                ev_resolver = build_resolver(ev.platform, client=client)
                hydrated = await ev_resolver.fetch_event_metadata(ev.canonical_url)
            merged_meta = {**(ev.raw_metadata or {}), **(hydrated.raw_metadata or {})}
            return hydrated.model_copy(update={"raw_metadata": merged_meta})
        except Exception:
            return ev

    hydrated_events = list(
        await asyncio.gather(
            *[
                _hydrate(ev, allowed=index < SEARCH_HYDRATE_LIMIT)
                for index, ev in enumerate(top_events)
            ]
        )
    )

    known_by_id = {ev.id: ev for ev in known}
    hydrated_events = [_keep_stored_detail(ev, known_by_id.get(ev.id)) for ev in hydrated_events]

    if hydrated_events:
        try:
            async with db.session() as session:
                repo = EventRepository(session)
                for ev in hydrated_events:
                    await repo.upsert_event(ev)
        except Exception as exc:
            logger.warning("hydrated_event_upsert_failed", error=str(exc))

    active_events = [
        ev
        for ev in hydrated_events
        if (ev.status.value if hasattr(ev.status, "value") else str(ev.status))
        != EventStatus.CLOSED.value
    ]

    # 需要瀏覽器才補得到的活動，排一張背景 job 給 Worker；這一次的結果照舊先回，
    # 下一次搜尋同一場就有活動說明了。不等它，搜尋不該為了描述卡住十幾秒。
    broker = getattr(request.app.state, "broker", None)
    if broker is not None:
        await _queue_browser_hydration(
            active_events[:SEARCH_HYDRATE_LIMIT], broker=broker, settings=settings
        )

    return EventSearchResponse(
        query=query,
        results=[_event_to_search_result(ev) for ev in active_events],
    )


async def _queue_browser_hydration(
    events: list[Event], *, broker: SqliteTaskBroker, settings: ApiSettings
) -> None:
    for ev in events:
        if not (ev.raw_metadata or {}).get("needs_browser_detail"):
            continue
        if ev.id in _hydration_requested:
            continue
        _hydration_requested.add(ev.id)
        try:
            await broker.enqueue(
                kind=JobKind.EVENT_HYDRATE,
                profile=settings.default_profile,
                payload={
                    "platform": ev.platform.value,
                    "slug": ev.event_slug,
                    "event_id": ev.id,
                    "canonical_url": ev.canonical_url,
                },
            )
        except Exception as exc:  # noqa: BLE001 — 補資料失敗不該讓搜尋失敗
            _hydration_requested.discard(ev.id)
            logger.warning("event_hydrate_enqueue_failed", event_id=ev.id, error=str(exc))


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
