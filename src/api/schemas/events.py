from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResolveEventRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1)
    orgs: list[str] | None = None
    persist: bool = True


class TicketTypeOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    price: int
    status: str
    remaining_count: int | None = None
    raw_id: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    platform: str
    organizer: str
    event_slug: str
    title: str
    canonical_url: str
    status: str
    sale_start_at: datetime | None = None
    sale_end_at: datetime | None = None
    event_start_at: datetime | None = None
    ticket_types: list[TicketTypeOut] = Field(default_factory=list)
    raw_metadata: dict[str, Any] | None = None


class EventCandidateOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    url: str
    score: float
    matched_by: str = "search"


class ResolveEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    auto_selected: bool
    event: EventOut | None = None
    candidates: list[EventCandidateOut] = Field(default_factory=list)
