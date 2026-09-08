from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from domain.event import (
    Event,
    EventStatus,
    PlatformEnum,
    TicketType,
    TicketTypeStatus,
)
from domain.preference import TicketPreference, TicketPriority
from domain.task import (
    CreditCardProfile,
    PaymentMethod,
    PurchaseTaskSpec,
    TaskStatus,
    UserContactProfile,
)
from storage.database import Database
from storage.models import EventModel
from storage.repositories import EventRepository, TaskRepository

SALE_START = datetime(2026, 1, 10, 4, 0, tzinfo=timezone.utc)
EVENT_START = datetime(2026, 3, 14, 11, 30, tzinfo=timezone.utc)
EXPECTED_TABLES = {
    "events",
    "ticket_types",
    "purchase_tasks",
    "experiments",
    "experiment_events",
    "experiment_metrics",
}
CARD_NUMBER = "4111111111111234"


def make_event(*, ticket_count: int = 2) -> Event:
    event_id = Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2026")
    names = ["預售全區站席", "搖滾區站席", "學生優惠票"][:ticket_count]
    return Event(
        id=event_id,
        platform=PlatformEnum.KKTIX,
        organizer="atarayo",
        event_slug="atarayo-taipei-2026",
        title="Atarayo Asia Tour 2026 — Taipei",
        canonical_url="https://atarayo.kktix.cc/events/atarayo-taipei-2026",
        sale_start_at=SALE_START,
        event_start_at=EVENT_START,
        status=EventStatus.ON_SALE,
        raw_metadata={"organizer_display": "Atarayo Live"},
        ticket_types=[
            TicketType(
                id=TicketType.make_id(event_id, name),
                event_id=event_id,
                name=name,
                price=2800 + index * 1000,
                status=TicketTypeStatus.AVAILABLE,
            )
            for index, name in enumerate(names)
        ],
    )


def make_spec(task_id: str = "task_0001") -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id=task_id,
        event_title="Atarayo Asia Tour 2026 — Taipei",
        event_url="https://atarayo.kktix.cc/events/atarayo-taipei-2026",
        sale_start_at=SALE_START,
        ticket_preference=TicketPreference(
            priorities=[TicketPriority(price=2800, priority=1)]
        ),
        contact_profile=UserContactProfile(
            name="王小明", phone="0912345678", email="ming@example.com"
        ),
        payment_method=PaymentMethod.CREDIT_CARD,
        payment_profile=CreditCardProfile(
            card_number=CARD_NUMBER,
            expiry_month="09",
            expiry_year="29",
            cvv="987",
            cardholder_name="WANG XIAO MING",
        ),
    )


def test_in_memory_database_is_rejected() -> None:
    with pytest.raises(ValueError):
        Database(":memory:")


def test_empty_path_database_is_rejected() -> None:
    with pytest.raises(ValueError):
        Database("")


async def test_sqlite_pragmas(db: Database) -> None:
    async with db.session() as session:
        journal_mode = (await session.execute(text("PRAGMA journal_mode"))).scalar()
        foreign_keys = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
        busy_timeout = (await session.execute(text("PRAGMA busy_timeout"))).scalar()
    assert str(journal_mode).lower() == "wal"
    assert int(foreign_keys) == 1
    assert int(busy_timeout) == 5000


async def test_create_all_creates_six_tables(db: Database) -> None:
    async with db.session() as session:
        rows = (
            await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        ).all()
    names = {row[0] for row in rows}
    assert names >= EXPECTED_TABLES


async def test_database_file_is_created_on_disk(
    db: Database, tmp_db_path: Path
) -> None:
    assert tmp_db_path.exists()
    assert db.db_path == tmp_db_path.resolve()


async def test_upsert_then_get_by_id(db: Database) -> None:
    event = make_event()
    async with db.session() as session:
        saved = await EventRepository(session).upsert_event(event)
    assert saved.id == event.id

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(event.id)

    assert loaded is not None
    assert loaded.title == event.title
    assert loaded.platform is PlatformEnum.KKTIX
    assert loaded.status is EventStatus.ON_SALE
    assert loaded.raw_metadata == {"organizer_display": "Atarayo Live"}
    assert len(loaded.ticket_types) == 2


async def test_timezone_roundtrip_is_utc(db: Database) -> None:
    event = make_event()
    async with db.session() as session:
        await EventRepository(session).upsert_event(event)

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(event.id)

    assert loaded is not None
    assert loaded.sale_start_at == SALE_START
    assert loaded.event_start_at == EVENT_START
    assert loaded.sale_start_at.tzinfo is timezone.utc
    assert loaded.event_start_at.tzinfo is timezone.utc


async def test_upsert_is_idempotent(db: Database) -> None:
    event = make_event()
    async with db.session() as session:
        repo = EventRepository(session)
        await repo.upsert_event(event)
    async with db.session() as session:
        await EventRepository(session).upsert_event(event)

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(event.id)

    assert loaded is not None
    assert len(loaded.ticket_types) == 2


async def test_upsert_replaces_ticket_types(db: Database) -> None:
    async with db.session() as session:
        await EventRepository(session).upsert_event(make_event(ticket_count=3))
    async with db.session() as session:
        await EventRepository(session).upsert_event(make_event(ticket_count=1))

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(
            Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2026")
        )
        remaining = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_types"))
        ).scalar()

    assert loaded is not None
    assert len(loaded.ticket_types) == 1
    assert int(remaining) == 1


async def test_get_by_canonical_url_and_list_by_platform(db: Database) -> None:
    event = make_event()
    async with db.session() as session:
        await EventRepository(session).upsert_event(event)

    async with db.session() as session:
        repo = EventRepository(session)
        by_url = await repo.get_by_canonical_url(event.canonical_url)
        listed = await repo.list_by_platform(PlatformEnum.KKTIX)
        empty = await repo.list_by_platform(PlatformEnum.TIXCRAFT)
        missing = await repo.get_by_canonical_url("https://x.kktix.cc/events/none")

    assert by_url is not None
    assert by_url.id == event.id
    assert [e.id for e in listed] == [event.id]
    assert empty == []
    assert missing is None


async def test_delete_event_cascades(db: Database) -> None:
    event = make_event()
    async with db.session() as session:
        await EventRepository(session).upsert_event(event)

    async with db.session() as session:
        assert await EventRepository(session).delete_event(event.id) is True

    async with db.session() as session:
        repo = EventRepository(session)
        assert await repo.get_by_id(event.id) is None
        assert await repo.delete_event(event.id) is False
        remaining = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_types"))
        ).scalar()
    assert int(remaining) == 0


async def test_task_spec_json_has_no_card_number(db: Database) -> None:
    spec = make_spec()
    async with db.session() as session:
        task_id = await TaskRepository(session).create(spec)

    async with db.session() as session:
        raw = (
            await session.execute(
                text("SELECT spec FROM purchase_tasks WHERE id = :id"),
                {"id": task_id},
            )
        ).scalar_one()

    raw_text = raw if isinstance(raw, str) else json.dumps(raw)
    assert CARD_NUMBER not in raw_text
    assert "card_number" not in raw_text
    assert "cvv" not in raw_text
    assert "payment_profile" not in raw_text


async def test_task_create_and_get(db: Database) -> None:
    spec = make_spec()
    async with db.session() as session:
        await TaskRepository(session).create(spec, scheduled_at=SALE_START)

    async with db.session() as session:
        record = await TaskRepository(session).get(spec.task_id)

    assert record is not None
    assert record.status is TaskStatus.CREATED
    assert record.scheduled_at == SALE_START
    assert record.created_at.tzinfo is timezone.utc
    assert record.spec["task_id"] == spec.task_id


async def test_task_update_status_timestamps(db: Database) -> None:
    spec = make_spec()
    async with db.session() as session:
        await TaskRepository(session).create(spec)

    async with db.session() as session:
        assert await TaskRepository(session).update_status(
            spec.task_id, TaskStatus.RUNNING
        )

    async with db.session() as session:
        running = await TaskRepository(session).get(spec.task_id)
    assert running is not None
    assert running.status is TaskStatus.RUNNING
    assert running.started_at is not None
    assert running.started_at.tzinfo is timezone.utc
    assert running.finished_at is None

    async with db.session() as session:
        await TaskRepository(session).update_status(
            spec.task_id, TaskStatus.COMPLETED, error_message=None
        )

    async with db.session() as session:
        done = await TaskRepository(session).get(spec.task_id)
    assert done is not None
    assert done.status is TaskStatus.COMPLETED
    assert done.finished_at is not None
    assert done.finished_at.tzinfo is timezone.utc
    assert done.started_at == running.started_at


async def test_task_update_status_missing_task(db: Database) -> None:
    async with db.session() as session:
        assert (
            await TaskRepository(session).update_status("nope", TaskStatus.FAILED)
            is False
        )
        assert await TaskRepository(session).get("nope") is None


async def test_task_list_by_status(db: Database) -> None:
    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.create(make_spec("task_a"))
        await repo.create(make_spec("task_b"))

    async with db.session() as session:
        await TaskRepository(session).update_status(
            "task_b", TaskStatus.FAILED, error_message="boom"
        )

    async with db.session() as session:
        repo = TaskRepository(session)
        created = await repo.list_by_status(TaskStatus.CREATED)
        failed = await repo.list_by_status(TaskStatus.FAILED)

    assert [record.id for record in created] == ["task_a"]
    assert [record.id for record in failed] == ["task_b"]
    assert failed[0].error_message == "boom"


EVENT_ID = Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2026")


async def test_upsert_recovers_from_concurrent_insert_race(db: Database) -> None:
    """TOCTOU: our SELECT saw nothing, another session inserted before our INSERT."""
    fired = False

    async with db.session() as session:
        repo = EventRepository(session)
        real_load = repo._load_orm

        async def racing_load(event_id: str) -> EventModel | None:
            nonlocal fired
            orm = await real_load(event_id)
            if not fired:
                fired = True
                async with db.session() as competitor:
                    await EventRepository(competitor).upsert_event(
                        make_event(ticket_count=1)
                    )
                return None
            return orm

        repo._load_orm = racing_load  # type: ignore[method-assign]
        saved = await repo.upsert_event(make_event(ticket_count=3))

    assert fired is True
    assert saved.id == EVENT_ID
    assert len(saved.ticket_types) == 3

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(EVENT_ID)
        event_rows = (
            await session.execute(text("SELECT COUNT(*) FROM events"))
        ).scalar()
        ticket_rows = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_types"))
        ).scalar()

    assert loaded is not None
    assert len(loaded.ticket_types) == 3
    assert int(event_rows) == 1
    assert int(ticket_rows) == 3


async def test_upsert_race_uses_savepoint_without_rolling_back_prior_flush(
    db: Database,
) -> None:
    """A prior flushed record in the same session must survive an IntegrityError in upsert_event."""
    # 1. Pre-insert the event in a competitor session
    async with db.session() as competitor:
        await EventRepository(competitor).upsert_event(make_event(ticket_count=1))

    spec = make_spec("task_prior_flush")

    async with db.session() as session:
        task_repo = TaskRepository(session)
        event_repo = EventRepository(session)

        # 2. Add and flush a task record prior to the racing upsert
        task_id = await task_repo.create(spec)

        # 3. Simulate TOCTOU: first _load_orm returns None, triggering insert attempt and IntegrityError
        real_load = event_repo._load_orm
        first_call = True

        async def racing_load(event_id: str) -> EventModel | None:
            nonlocal first_call
            if first_call:
                first_call = False
                return None
            return await real_load(event_id)

        event_repo._load_orm = racing_load  # type: ignore[method-assign]
        saved_event = await event_repo.upsert_event(make_event(ticket_count=3))

        assert saved_event.id == EVENT_ID
        assert len(saved_event.ticket_types) == 3

    # 4. Verify both prior flushed task and resolved event successfully committed
    async with db.session() as session:
        persisted_task = await TaskRepository(session).get(task_id)
        persisted_event = await EventRepository(session).get_by_id(EVENT_ID)

    assert persisted_task is not None
    assert persisted_task.id == "task_prior_flush"
    assert persisted_event is not None
    assert len(persisted_event.ticket_types) == 3


async def test_concurrent_upsert_from_two_sessions_is_idempotent(db: Database) -> None:
    async def writer(ticket_count: int) -> str:
        async with db.session() as session:
            saved = await EventRepository(session).upsert_event(
                make_event(ticket_count=ticket_count)
            )
            return saved.id

    ids = await asyncio.gather(writer(3), writer(2))
    assert ids == [EVENT_ID, EVENT_ID]

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(EVENT_ID)
        event_rows = (
            await session.execute(text("SELECT COUNT(*) FROM events"))
        ).scalar()
        ticket_rows = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_types"))
        ).scalar()

    assert loaded is not None
    assert int(event_rows) == 1
    assert len(loaded.ticket_types) in {2, 3}
    assert int(ticket_rows) == len(loaded.ticket_types)


async def test_upsert_preserves_tickets_when_incoming_list_is_empty(
    db: Database,
) -> None:
    async with db.session() as session:
        await EventRepository(session).upsert_event(make_event(ticket_count=3))

    stripped = make_event(ticket_count=3).model_copy(update={"ticket_types": []})
    async with db.session() as session:
        saved = await EventRepository(session).upsert_event(stripped)
    assert len(saved.ticket_types) == 3

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(EVENT_ID)
        ticket_rows = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_types"))
        ).scalar()

    assert loaded is not None
    assert len(loaded.ticket_types) == 3
    assert int(ticket_rows) == 3


async def test_upsert_can_opt_out_of_ticket_preservation(db: Database) -> None:
    async with db.session() as session:
        await EventRepository(session).upsert_event(make_event(ticket_count=3))

    stripped = make_event(ticket_count=3).model_copy(update={"ticket_types": []})
    async with db.session() as session:
        await EventRepository(session).upsert_event(
            stripped, preserve_tickets_on_empty=False
        )

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(EVENT_ID)
        ticket_rows = (
            await session.execute(text("SELECT COUNT(*) FROM ticket_types"))
        ).scalar()

    assert loaded is not None
    assert loaded.ticket_types == []
    assert int(ticket_rows) == 0


async def test_first_insert_with_empty_tickets_still_persists_event(
    db: Database,
) -> None:
    stripped = make_event(ticket_count=1).model_copy(update={"ticket_types": []})
    async with db.session() as session:
        saved = await EventRepository(session).upsert_event(stripped)
    assert saved.ticket_types == []

    async with db.session() as session:
        loaded = await EventRepository(session).get_by_id(EVENT_ID)
    assert loaded is not None
    assert loaded.ticket_types == []
