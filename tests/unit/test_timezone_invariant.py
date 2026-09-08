from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from adapters.ticketing.kktix.resolver import (
    TAIPEI_TZ,
    KKTIXParseError,
    _parse_datetime,
)
from domain.event import Event, PlatformEnum
from domain.preference import TicketPreference, TicketPriority
from domain.task import PaymentMethod, PurchaseTaskSpec, UserContactProfile
from storage.database import Database
from storage.models import EventModel

NAIVE = datetime(2026, 1, 10, 12, 0)
TAIPEI_NOON = datetime(2026, 1, 10, 12, 0, tzinfo=TAIPEI_TZ)
EXPECTED_UTC = datetime(2026, 1, 10, 4, 0, tzinfo=timezone.utc)


def make_event(**overrides: object) -> Event:
    payload: dict[str, object] = {
        "id": "ev_tz",
        "platform": PlatformEnum.KKTIX,
        "organizer": "atarayo",
        "event_slug": "atarayo-taipei-2026",
        "title": "tz test",
        "canonical_url": "https://atarayo.kktix.cc/events/atarayo-taipei-2026",
    }
    payload.update(overrides)
    return Event(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["sale_start_at", "event_start_at", "created_at"])
def test_event_rejects_naive_datetime(field: str) -> None:
    with pytest.raises(ValidationError):
        make_event(**{field: NAIVE})


def test_purchase_task_spec_rejects_naive_sale_start() -> None:
    with pytest.raises(ValidationError):
        PurchaseTaskSpec(
            task_id="task_tz",
            event_title="tz",
            event_url="https://atarayo.kktix.cc/events/atarayo-taipei-2026",
            sale_start_at=NAIVE,
            ticket_preference=TicketPreference(priorities=[TicketPriority(price=0)]),
            contact_profile=UserContactProfile(
                name="A", phone="0912345678", email="a@b.tw"
            ),
            payment_method=PaymentMethod.MOCK,
        )


@pytest.mark.parametrize("field", ["sale_start_at", "event_start_at"])
def test_event_converts_taipei_to_utc(field: str) -> None:
    event = make_event(**{field: TAIPEI_NOON})
    value = getattr(event, field)
    assert value == EXPECTED_UTC
    assert value.tzinfo is timezone.utc


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "2026-03-14T19:30:00+08:00",
            datetime(2026, 3, 14, 11, 30, tzinfo=timezone.utc),
        ),
        (
            "2026/03/14 (週六) 19:30(+0800)",
            datetime(2026, 3, 14, 11, 30, tzinfo=timezone.utc),
        ),
        ("2026/03/14 19:30", datetime(2026, 3, 14, 11, 30, tzinfo=timezone.utc)),
        ("2026-03-14 19:30:00", datetime(2026, 3, 14, 11, 30, tzinfo=timezone.utc)),
        ("2026/03/14", datetime(2026, 3, 13, 16, 0, tzinfo=timezone.utc)),
        (
            "2026/03/14 19:30 GMT+00:00",
            datetime(2026, 3, 14, 19, 30, tzinfo=timezone.utc),
        ),
        (
            "2026/03/14 (週六) 19:30(-0500)",
            datetime(2026, 3, 15, 0, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_parse_datetime_always_returns_utc(text: str, expected: datetime) -> None:
    parsed = _parse_datetime(text)
    assert parsed.tzinfo is timezone.utc
    assert parsed == expected
    assert parsed.utcoffset() == timedelta(0)


def test_parse_datetime_rejects_garbage() -> None:
    with pytest.raises(KKTIXParseError):
        _parse_datetime("not a date")


async def test_storage_boundary_rejects_naive_datetime(db: Database) -> None:
    with pytest.raises(StatementError) as excinfo:
        async with db.session() as session:
            session.add(
                EventModel(
                    id="ev_naive",
                    platform="kktix",
                    organizer="atarayo",
                    event_slug="naive",
                    title="naive",
                    canonical_url="https://atarayo.kktix.cc/events/naive",
                    status="UNKNOWN",
                    sale_start_at=NAIVE,
                )
            )
            await session.flush()

    assert isinstance(excinfo.value.orig, ValueError)
    assert "naive datetime rejected at storage boundary" in str(excinfo.value.orig)


async def test_storage_roundtrip_keeps_utc(db: Database) -> None:
    async with db.session() as session:
        session.add(
            EventModel(
                id="ev_utc",
                platform="kktix",
                organizer="atarayo",
                event_slug="utc",
                title="utc",
                canonical_url="https://atarayo.kktix.cc/events/utc",
                status="UNKNOWN",
                sale_start_at=TAIPEI_NOON,
            )
        )

    async with db.session() as session:
        stored = (
            await session.execute(select(EventModel).where(EventModel.id == "ev_utc"))
        ).scalar_one()
        assert stored.sale_start_at == EXPECTED_UTC
        assert stored.sale_start_at.tzinfo is timezone.utc
        assert stored.created_at.tzinfo is timezone.utc
