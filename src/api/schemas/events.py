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


class TicketingProviderOut(BaseModel):
    """售票的平台。

    一定是陣列：同一場活動未來可能同時掛在多個平台，即使目前只有 KKTIX。
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    event_url: str


class EventOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    platform: str
    organizer: str
    organizer_name: str | None = None
    """主辦單位的顯示名稱；`organizer` 是內部代號，不給使用者看。"""
    event_slug: str
    title: str
    description: str | None = None
    canonical_url: str
    ticketing_providers: list[TicketingProviderOut] = Field(default_factory=list)
    status: str
    sale_start_at: datetime | None = None
    sale_end_at: datetime | None = None
    event_start_at: datetime | None = None
    ticket_types: list[TicketTypeOut] = Field(default_factory=list)
    detail_loaded: bool = False
    """False 代表只有搜尋層的淺資料，票種與開賣時間尚未由活動頁補齊。"""
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


class EventSearchResultOut(BaseModel):
    """搜尋結果的單一活動。

    `id` 是可直接放進網址的穩定活動識別碼（由平台＋主辦＋活動代稱雜湊而來），
    不是陣列序號、標題或前端臨時值。
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str | None = None
    organizer: str | None = None
    ticketing_providers: list[TicketingProviderOut] = Field(default_factory=list)
    canonical_url: str
    sale_start_at: datetime | None = None
    sale_end_at: datetime | None = None
    event_start_at: datetime | None = None
    status: str
    detail_loaded: bool = Field(
        default=False,
        description="票種與開賣時間是否已由活動頁補齊。",
    )


class EventSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    results: list[EventSearchResultOut] = Field(default_factory=list)


class EventStatusOut(BaseModel):
    """一場活動目前的售票狀態。

    搜尋不等狀態算完就先把活動回給前端，狀態由這支端點分批補。`checked_at` 有值
    才代表「已經真的去確認過賣不賣得到票」——在那之前 `status` 只是還沒問到底的
    中間值，前端據此決定要不要繼續問。
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    status: str
    sale_start_at: datetime | None = None
    sale_end_at: datetime | None = None
    event_start_at: datetime | None = None
    detail_loaded: bool = False
    checked_at: datetime | None = Field(
        default=None,
        description="狀態最後一次被實際確認的時間；未確認過為 null。",
    )


class EventStatusesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[EventStatusOut] = Field(default_factory=list)
    pending: bool = Field(
        default=False,
        description="後端是否還在補票況；false 代表不會再有新結果了。",
    )
