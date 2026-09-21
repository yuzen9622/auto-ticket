from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from domain.types import DomainBaseModel


class TicketPriority(DomainBaseModel):
    price: int = Field(ge=0)
    ticket_name_pattern: str | None = None
    priority: int = Field(default=1, ge=1)


class TicketRule(DomainBaseModel):
    """不需要事先知道票價也寫得出來的挑票規則。

    拓元與 ibon 的票種與票價要進到票區頁才看得到，開賣前根本填不出
    `TicketPriority` 要求的精確價格。少了這組規則，開賣前唯一寫得出來的設定就是
    `fallback_to_any`——而那是「頁面上第一個可選的就買」，等於把選票交給版面順序。

    規則只描述「什麼樣的票我要」，開賣瞬間讀到真實票種清單才套用。
    """

    max_price: int | None = Field(default=None, ge=0)
    min_price: int | None = Field(default=None, ge=0)
    prefer_name_patterns: list[str] = Field(default_factory=list)
    """名稱關鍵字，越前面越優先；命中的一律排在未命中的前面。子字串比對，不是正則。"""
    exclude_name_patterns: list[str] = Field(default_factory=list)
    """名稱命中即完全排除。優待票與身障票入場要查驗證件，資格不符當場作廢，
    所以排除必須是完全性的——連 `fallback_to_any` 都不得繞過。"""
    price_order: Literal["page", "highest", "lowest"] = "page"
    """同樣可買時的取捨：版面順序、貴的優先（搶好位子）或便宜的優先。"""

    @model_validator(mode="after")
    def _price_window_is_ordered(self) -> TicketRule:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price 不得大於 max_price")
        return self

    @property
    def has_price_window(self) -> bool:
        return self.max_price is not None or self.min_price is not None

    def accepts_price(self, price: int) -> bool:
        if self.max_price is not None and price > self.max_price:
            return False
        return not (self.min_price is not None and price < self.min_price)


class SeatPreference(DomainBaseModel):
    adjacent: bool = True
    strategy: Literal["best_available", "same_zone", "specific_zone"] = "best_available"
    preferred_zones: list[str] = Field(default_factory=list)


class TicketPreference(DomainBaseModel):
    quantity: int = Field(default=2, ge=1, le=10)
    priorities: list[TicketPriority] = Field(default_factory=list)
    """精確價格比對。開賣前拿不到票價的平台填不出來，所以允許整個留空。"""
    seat_preference: SeatPreference = Field(default_factory=SeatPreference)
    fallback_to_any: bool = False
    rule: TicketRule | None = None
    """精確價格全部落空後、退回 `fallback_to_any` 之前的那一關。"""

    @model_validator(mode="after")
    def _can_pick_something(self) -> TicketPreference:
        """三種挑票方式至少要有一種。

        `priorities` 原本要求至少一筆，是因為那時它是唯一的挑票方式。改成可以留空
        之後，真正要守的不變式變成「這份偏好有沒有辦法挑到任何一張票」——三種都
        沒有的話送出去的是一個保證買不到票的任務，那比擋下來更糟。
        """
        if not self.priorities and self.rule is None and not self.fallback_to_any:
            raise ValueError(
                "priorities、rule、fallback_to_any 至少要有一種，否則挑不到任何票"
            )
        return self

    @property
    def sorted_priorities(self) -> list[TicketPriority]:
        return sorted(self.priorities, key=lambda p: (p.priority, -p.price))
