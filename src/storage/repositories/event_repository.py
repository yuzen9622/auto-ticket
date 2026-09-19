from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.event import Event, EventStatus, PlatformEnum, TicketType, TicketTypeStatus
from storage.models import EventModel, TicketTypeModel


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_event(
        self, event: Event, *, preserve_tickets_on_empty: bool = True
    ) -> Event:
        orm = await self._load_orm(event.id)

        if orm is None:
            orm = EventModel(id=event.id)
            self._apply_domain(orm, event, preserve_tickets_on_empty)
            try:
                async with self._session.begin_nested():
                    self._session.add(orm)
                    await self._session.flush()
            except IntegrityError:
                # Another session inserted this id between our SELECT and INSERT.
                # Savepoint was rolled back, outer transaction remains active.
                existing = await self._load_orm(event.id)
                if existing is None:
                    raise
                self._apply_domain(
                    existing,
                    event,
                    preserve_tickets_on_empty=preserve_tickets_on_empty,
                )
                existing.updated_at = datetime.now(timezone.utc)
                await self._session.flush()
                orm = existing
        else:
            self._apply_domain(orm, event, preserve_tickets_on_empty)
            orm.updated_at = datetime.now(timezone.utc)
            await self._session.flush()

        return self._to_domain(orm)

    async def _load_orm(self, event_id: str) -> EventModel | None:
        stmt = (
            select(EventModel)
            .where(EventModel.id == event_id)
            .options(selectinload(EventModel.ticket_types))
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, event_id: str) -> Event | None:
        stmt = (
            select(EventModel)
            .where(EventModel.id == event_id)
            .options(selectinload(EventModel.ticket_types))
        )
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_domain(orm) if orm is not None else None

    async def get_by_canonical_url(self, url: str) -> Event | None:
        stmt = (
            select(EventModel)
            .where(EventModel.canonical_url == url)
            .options(selectinload(EventModel.ticket_types))
        )
        orm = (await self._session.execute(stmt)).scalars().first()
        return self._to_domain(orm) if orm is not None else None

    async def list_by_platform(self, platform: PlatformEnum) -> list[Event]:
        stmt = (
            select(EventModel)
            .where(EventModel.platform == platform.value)
            .options(selectinload(EventModel.ticket_types))
            .order_by(EventModel.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_domain(orm) for orm in rows]

    async def delete_event(self, event_id: str) -> bool:
        stmt = (
            select(EventModel)
            .where(EventModel.id == event_id)
            .options(selectinload(EventModel.ticket_types))
        )
        orm = (await self._session.execute(stmt)).scalar_one_or_none()
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.flush()
        return True

    def _apply_domain(
        self, orm: EventModel, event: Event, preserve_tickets_on_empty: bool
    ) -> None:
        orm.platform = event.platform.value
        orm.organizer = event.organizer
        orm.event_slug = event.event_slug
        orm.title = event.title
        orm.canonical_url = event.canonical_url
        orm.sale_start_at = event.sale_start_at
        orm.sale_end_at = event.sale_end_at
        orm.event_start_at = event.event_start_at
        orm.status = event.status.value
        orm.raw_metadata = event.raw_metadata
        if not event.ticket_types and preserve_tickets_on_empty and orm.ticket_types:
            return
        # Whole-collection assignment (not clear()) keeps the relationship loaded even
        # when the new list is empty, so _to_domain never triggers a lazy load.
        orm.ticket_types = [
            TicketTypeModel(
                id=ticket.id,
                event_id=event.id,
                name=ticket.name,
                price=ticket.price,
                status=ticket.status.value,
                inventory_estimate=ticket.inventory_estimate,
                raw_id=ticket.raw_id,
            )
            for ticket in event.ticket_types
        ]

    def _to_domain(self, orm: EventModel) -> Event:
        return Event(
            id=orm.id,
            platform=PlatformEnum(orm.platform),
            organizer=orm.organizer,
            event_slug=orm.event_slug,
            title=orm.title,
            canonical_url=orm.canonical_url,
            sale_start_at=orm.sale_start_at,
            sale_end_at=getattr(orm, "sale_end_at", None),
            event_start_at=orm.event_start_at,
            status=EventStatus(orm.status),
            raw_metadata=orm.raw_metadata or {},
            ticket_types=[
                TicketType(
                    id=tt.id,
                    event_id=orm.id,
                    name=tt.name,
                    price=tt.price,
                    status=TicketTypeStatus(tt.status),
                    inventory_estimate=tt.inventory_estimate,
                    raw_id=tt.raw_id,
                )
                for tt in orm.ticket_types
            ],
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )
