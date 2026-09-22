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
    """活動的售票狀態。

    對外只呈現 `ANNOUNCED`（尚未開賣）與 `ON_SALE`（販售中）；買不到的一律
    `CLOSED`，不會出現在搜尋結果裡。

    `SOLD_OUT` 已經不再由任何解析器產生——三個平台的售完都只寫在購票頁上，那些
    頁面一律擋掉無頭瀏覽器，得借使用者本機的 Chrome 才讀得到，不值得為了一個狀態
    在背景彈視窗。保留這個值只為了讀得動先前寫進資料庫的舊資料；那些列會在下一次
    補票況時被改寫掉。
    """

    ANNOUNCED = "ANNOUNCED"
    ON_SALE = "ON_SALE"
    SOLD_OUT = "SOLD_OUT"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class TicketTypeStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    SOLD_OUT = "SOLD_OUT"
    COMING_SOON = "COMING_SOON"
    CLOSED = "CLOSED"


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
        return digest


class Event(DomainBaseModel):
    id: str
    platform: PlatformEnum
    organizer: str
    event_slug: str
    title: str
    canonical_url: str
    sale_start_at: UtcDatetime | None = None
    sale_end_at: UtcDatetime | None = None
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
        return digest


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
