from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from domain.event import Event, EventCandidate, ResolveResult
from domain.preference import SeatPreference, TicketPreference
from domain.task import CreditCardProfile, UserContactProfile

if TYPE_CHECKING:
    from playwright.async_api import Page

    from adapters.payment.base import PaymentResult
else:
    Page = Any
    PaymentResult = Any


class TicketingAdapter(ABC):
    """Platform-specific purchase flow port.

    Concrete implementations live under ``adapters/ticketing/<platform>/``.
    """

    @abstractmethod
    async def navigate_to_event(self, page: Page, event_url: str) -> bool:
        """進入活動主頁或報名頁面"""
        raise NotImplementedError

    @abstractmethod
    async def detect_sale_opened(self, page: Page, timeout_ms: int) -> bool:
        """偵測頁面開賣狀態（輪詢或元素變更監聽）"""
        raise NotImplementedError

    @abstractmethod
    async def select_tickets(
        self, page: Page, preference: TicketPreference
    ) -> tuple[bool, str]:
        """依照票種策略選取票種與數量"""
        raise NotImplementedError

    @abstractmethod
    async def handle_seat_selection(
        self, page: Page, preference: SeatPreference
    ) -> bool:
        """處理劃位流程（電腦自動配位或指定選位）"""
        raise NotImplementedError

    @abstractmethod
    async def fill_contact_form(self, page: Page, profile: UserContactProfile) -> bool:
        """填寫聯絡人姓名、電話、Email 等資料並同意條款"""
        raise NotImplementedError

    @abstractmethod
    async def handle_verification(self, page: Page) -> bool:
        """偵測並處理驗證問題（文字問答題或圖形驗證碼）"""
        raise NotImplementedError

    @abstractmethod
    async def execute_payment(
        self, page: Page, payment_profile: CreditCardProfile | None
    ) -> PaymentResult:
        """把付款頁交給注入的 PaymentProvider，並原樣回傳其結果。

        實作**不得**自行決定要不要送出：是否發動金流完全由 provider 決定
        （預設 provider 停在送出鈕之前）。回傳 `PaymentResult` 而非 bool，
        是因為協調器需要用 `PaymentOutcome` 對應 FSM 事件。
        """
        raise NotImplementedError


class EventResolver(ABC):
    """Search / metadata port used to turn a user query into a canonical Event."""

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10) -> list[EventCandidate]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_event_metadata(self, event_url: str) -> Event:
        raise NotImplementedError

    @abstractmethod
    async def resolve(self, query: str) -> ResolveResult:
        raise NotImplementedError
