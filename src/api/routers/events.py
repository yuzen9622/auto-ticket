from __future__ import annotations

import asyncio
import re
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
from domain.event import Event, EventCandidate, EventStatus, PlatformEnum
from storage.database import Database
from storage.repositories.event_repository import EventRepository
from telemetry.logging import get_logger

router = APIRouter(prefix="/api/v1/events", tags=["events"])
logger = get_logger("api.events")

PROVIDER_NAMES = {"kktix": "KKTIX", "tixcraft": "拓元售票", "ibon": "ibon 售票"}
SEARCH_RESULT_LIMIT = 30
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
    if ev.platform == PlatformEnum.TIXCRAFT:
        return "拓元合作主辦單位"
    if ev.platform == PlatformEnum.IBON:
        return "ibon 合作主辦單位"
    return ev.organizer or None


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
    """淺資料只有搜尋層能給的欄位；票種或開賣時間出現過才算補齊。"""
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
    if candidate.organizer:
        raw_metadata["organizer_display"] = candidate.organizer
    return Event(
        id=Event.make_id(platform, org, slug),
        platform=platform,
        organizer=org,
        event_slug=slug,
        title=candidate.title,
        canonical_url=canonical_url,
        status=EventStatus.UNKNOWN,
        raw_metadata=raw_metadata,
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

    async def _hydrate(ev: Event) -> Event:
        if _is_detail_loaded(ev):
            return ev
        try:
            ev_resolver = build_resolver(ev.platform, client=client)
            hydrated = await ev_resolver.fetch_event_metadata(ev.canonical_url)
            merged_meta = {**(ev.raw_metadata or {}), **(hydrated.raw_metadata or {})}
            return hydrated.model_copy(update={"raw_metadata": merged_meta})
        except Exception:
            return ev

    hydrated_events = list(await asyncio.gather(*[_hydrate(ev) for ev in top_events]))

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

    return EventSearchResponse(
        query=query,
        results=[_event_to_search_result(ev) for ev in active_events],
    )


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


@router.get("/{event_id}", response_model=EventOut)
async def get_event(
    event_id: str,
    request: Request,
    db: Database = Depends(get_db),
) -> EventOut:
    async with db.session() as session:
        ev = await EventRepository(session).get_by_id(event_id)
    if ev is None:
        raise NotFoundError(f"Event {event_id} not found")

    if _is_detail_loaded(ev):
        return _event_to_out(ev)

    # 依活動的平台動態建構 resolver 進行 hydration
    client = getattr(request.app.state, "http_client", None)
    resolver = build_resolver(ev.platform, client=client)
    try:
        hydrated = await resolver.fetch_event_metadata(ev.canonical_url)
    except Exception:
        return _event_to_out(ev)

    merged_metadata = {**(ev.raw_metadata or {}), **(hydrated.raw_metadata or {})}
    hydrated = hydrated.model_copy(update={"raw_metadata": merged_metadata})
    async with db.session() as session:
        ev = await EventRepository(session).upsert_event(hydrated)
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
