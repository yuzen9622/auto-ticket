from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request

from adapters.ticketing.kktix.resolver import (
    KKTIXEventResolver,
    KKTIXResolveError,
)
from api.deps import get_db, get_resolver
from api.errors import (
    InvalidRequestError,
    NotFoundError,
    UnsupportedError,
    UpstreamFailedError,
)
from api.schemas.events import (
    EventCandidateOut,
    EventOut,
    ResolveEventRequest,
    ResolveEventResponse,
    TicketTypeOut,
)
from domain.event import Event, PlatformEnum
from storage.database import Database
from storage.repositories.event_repository import EventRepository

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _event_to_out(ev: Event) -> EventOut:
    return EventOut(
        id=ev.id,
        platform=ev.platform.value
        if hasattr(ev.platform, "value")
        else str(ev.platform),
        organizer=ev.organizer,
        event_slug=ev.event_slug,
        title=ev.title,
        canonical_url=ev.canonical_url,
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
        raw_metadata=dict(ev.raw_metadata or {}) if ev.raw_metadata else None,
    )


@router.post("/resolve", response_model=ResolveEventResponse)
async def resolve_event(
    req: ResolveEventRequest,
    request: Request,
    db: Database = Depends(get_db),
    default_resolver: KKTIXEventResolver = Depends(get_resolver),
) -> ResolveEventResponse:
    resolver = default_resolver
    if req.orgs is not None:
        client = getattr(request.app.state, "http_client", None)
        if isinstance(client, httpx.AsyncClient):
            resolver = KKTIXEventResolver(client, orgs=req.orgs)

    try:
        res = await resolver.resolve(req.query)
    except KKTIXResolveError as exc:
        if "organizer feed scope required" in str(exc).lower():
            raise InvalidRequestError(str(exc))
        raise UpstreamFailedError(str(exc))
    except Exception as exc:
        raise UpstreamFailedError(str(exc))

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
    db: Database = Depends(get_db),
) -> EventOut:
    async with db.session() as session:
        repo = EventRepository(session)
        ev = await repo.get_by_id(event_id)
        if ev is None:
            raise NotFoundError(f"Event {event_id} not found")
        return _event_to_out(ev)


@router.get("", response_model=list[EventOut])
async def list_events(
    platform: str = "kktix",
    db: Database = Depends(get_db),
) -> list[EventOut]:
    try:
        plat_enum = PlatformEnum(platform.lower())
    except ValueError:
        raise UnsupportedError(f"Platform {platform} is not supported")

    async with db.session() as session:
        repo = EventRepository(session)
        events = await repo.list_by_platform(plat_enum)
        return [_event_to_out(ev) for ev in events]
