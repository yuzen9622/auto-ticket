from __future__ import annotations

from typing import Literal

from pydantic import Field

from domain.types import DomainBaseModel


class TicketPriority(DomainBaseModel):
    price: int = Field(ge=0)
    ticket_name_pattern: str | None = None
    priority: int = Field(default=1, ge=1)


class SeatPreference(DomainBaseModel):
    adjacent: bool = True
    strategy: Literal["best_available", "same_zone", "specific_zone"] = "best_available"
    preferred_zones: list[str] = Field(default_factory=list)


class TicketPreference(DomainBaseModel):
    quantity: int = Field(default=2, ge=1, le=10)
    priorities: list[TicketPriority] = Field(min_length=1)
    seat_preference: SeatPreference = Field(default_factory=SeatPreference)
    fallback_to_any: bool = False

    @property
    def sorted_priorities(self) -> list[TicketPriority]:
        return sorted(self.priorities, key=lambda p: (p.priority, -p.price))
