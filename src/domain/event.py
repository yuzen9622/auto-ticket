from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

from pydantic import Field

from domain.types import DomainBaseModel, UtcDatetime


class PlatformEnum(str, Enum):
    KKTIX = "kktix"
    TIXCRAFT = "tixcraft"
    IBON = "ibon"


class EventStatus(str, Enum):
    ANNOUNCED = "ANNOUNCED"
    ON_SALE = "ON_SALE"
    SOLD_OUT = "SOLD_OUT"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class TicketTypeStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    SOLD_OUT = "SOLD_OUT"
    COMING_SOON = "COMING_SOON"


class TicketType(DomainBaseModel):
    id: str
    event_id: str
    name: str
    price: int = Field(ge=0)
    status: TicketTypeStatus
    inventory_estimate: int | None = None
    raw_id: str | None = None

    @staticmethod
    def make_id(event_id: str, key: str) -> str:
        digest = hashlib.sha1(
            f"{event_id}:{key}".encode(), usedforsecurity=False
        ).hexdigest()[:16]
        return f"tt_{digest}"


class Event(DomainBaseModel):
    id: str
    platform: PlatformEnum
    organizer: str
    event_slug: str
    title: str
    canonical_url: str
    sale_start_at: UtcDatetime | None = None
    event_start_at: UtcDatetime | None = None
    status: EventStatus = EventStatus.UNKNOWN
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    ticket_types: list[TicketType] = Field(default_factory=list)
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None

    @staticmethod
    def make_id(platform: PlatformEnum, organizer: str, event_slug: str) -> str:
        digest = hashlib.sha1(
            f"{platform.value}:{organizer}:{event_slug}".encode(), usedforsecurity=False
        ).hexdigest()[:16]
        return f"ev_{digest}"


class EventCandidate(DomainBaseModel):
    title: str
    url: str
    organizer: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    published: UtcDatetime | None = None
    summary: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ResolveResult(DomainBaseModel):
    query: str
    auto_selected: bool
    event: Event | None
    candidates: list[EventCandidate]
    threshold: float
