from __future__ import annotations

from datetime import timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from adapters.ticketing.kktix.resolver import (
    MATCH_THRESHOLD,
    KKTIXEventResolver,
    KKTIXParseError,
)
from domain.event import PlatformEnum
from storage.database import Database
from storage.repositories import EventRepository
from tests.conftest import EXPECTED_EVENT_START_UTC, EXPECTED_SALE_START_UTC


async def test_resolve_to_persist_end_to_end(
    kktix_client: httpx.AsyncClient, tmp_db_path: Path
) -> None:
    resolver = KKTIXEventResolver(kktix_client, orgs=["atarayo"])

    result = await resolver.resolve("Atarayo Asia Tour 2026 — Taipei")
    assert result.auto_selected is True
    assert result.threshold == MATCH_THRESHOLD
    assert result.query == "Atarayo Asia Tour 2026 — Taipei"

    event = result.event
    assert event is not None
    assert event.event_start_at == EXPECTED_EVENT_START_UTC
    assert event.sale_start_at == EXPECTED_SALE_START_UTC
    assert event.event_start_at is not None
    assert event.sale_start_at is not None
    assert event.event_start_at.date() != event.sale_start_at.date()
    assert len(event.ticket_types) == 3

    db = Database(tmp_db_path)
    await db.create_all()
    try:
        async with db.session() as s:
            saved = await EventRepository(s).upsert_event(event)
            assert saved.id == event.id

        async with db.session() as s:
            got = await EventRepository(s).get_by_id(event.id)

        assert got is not None
        assert len(got.ticket_types) == 3
        assert got.sale_start_at == EXPECTED_SALE_START_UTC
        assert got.event_start_at == EXPECTED_EVENT_START_UTC
        assert got.sale_start_at is not None
        assert got.event_start_at is not None
        assert got.sale_start_at.tzinfo is timezone.utc
        assert got.event_start_at.tzinfo is timezone.utc
        assert got.platform is PlatformEnum.KKTIX
    finally:
        await db.dispose()


async def test_resolve_to_persist_is_idempotent(
    kktix_client: httpx.AsyncClient, tmp_db_path: Path
) -> None:
    resolver = KKTIXEventResolver(kktix_client, orgs=["atarayo"])
    result = await resolver.resolve("Atarayo Asia Tour 2026 — Taipei")
    event = result.event
    assert event is not None

    db = Database(tmp_db_path)
    await db.create_all()
    try:
        for _ in range(2):
            async with db.session() as s:
                await EventRepository(s).upsert_event(event)

        async with db.session() as s:
            got = await EventRepository(s).get_by_id(event.id)
            ticket_rows = (
                await s.execute(text("SELECT COUNT(*) FROM ticket_types"))
            ).scalar()

        assert got is not None
        assert len(got.ticket_types) == 3
        assert int(ticket_rows) == 3
    finally:
        await db.dispose()


async def test_low_score_resolve_persists_nothing(
    kktix_client: httpx.AsyncClient, tmp_db_path: Path
) -> None:
    resolver = KKTIXEventResolver(kktix_client, orgs=["atarayo"])
    result = await resolver.resolve("完全不相關的關鍵字")
    assert result.auto_selected is False
    assert result.event is None

    db = Database(tmp_db_path)
    await db.create_all()
    try:
        async with db.session() as s:
            event_rows = (await s.execute(text("SELECT COUNT(*) FROM events"))).scalar()
            ticket_rows = (
                await s.execute(text("SELECT COUNT(*) FROM ticket_types"))
            ).scalar()
        assert int(event_rows) == 0
        assert int(ticket_rows) == 0
    finally:
        await db.dispose()


async def test_dom_breakage_never_wipes_persisted_ticket_types(
    kktix_client: httpx.AsyncClient, tmp_db_path: Path
) -> None:
    """A resolver-side empty ticket list must not clear rows already in the DB."""
    resolver = KKTIXEventResolver(kktix_client, orgs=["atarayo"])
    result = await resolver.resolve("Atarayo Asia Tour 2026 — Taipei")
    event = result.event
    assert event is not None
    assert len(event.ticket_types) == 3

    db = Database(tmp_db_path)
    await db.create_all()
    try:
        async with db.session() as s:
            await EventRepository(s).upsert_event(event)

        degraded = event.model_copy(update={"ticket_types": []})
        async with db.session() as s:
            saved = await EventRepository(s).upsert_event(degraded)
        assert len(saved.ticket_types) == 3

        async with db.session() as s:
            got = await EventRepository(s).get_by_id(event.id)
            ticket_rows = (
                await s.execute(text("SELECT COUNT(*) FROM ticket_types"))
            ).scalar()

        assert got is not None
        assert len(got.ticket_types) == 3
        assert int(ticket_rows) == 3
        assert {t.name for t in got.ticket_types} == {
            t.name for t in event.ticket_types
        }
    finally:
        await db.dispose()


async def test_ticket_block_breakage_aborts_before_persisting(
    tmp_db_path: Path,
) -> None:
    """DOM drift raises instead of returning [], so the empty list never reaches the DB."""
    broken_page = (
        "<html><body>"
        '<div class="header-title"><h1>Atarayo Asia Tour 2026 — Taipei</h1></div>'
        '<div class="tickets"><table><tbody>'
        '<tr><td class="renamed">預售全區站席</td></tr>'
        "</tbody></table></div>"
        "</body></html>"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=broken_page)

    db = Database(tmp_db_path)
    await db.create_all()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = KKTIXEventResolver(client, orgs=["atarayo"])
            with pytest.raises(KKTIXParseError):
                await resolver.fetch_event_metadata(
                    "https://atarayo.kktix.cc/events/atarayo-taipei-2026"
                )

        async with db.session() as s:
            event_rows = (await s.execute(text("SELECT COUNT(*) FROM events"))).scalar()
            ticket_rows = (
                await s.execute(text("SELECT COUNT(*) FROM ticket_types"))
            ).scalar()
        assert int(event_rows) == 0
        assert int(ticket_rows) == 0
    finally:
        await db.dispose()
