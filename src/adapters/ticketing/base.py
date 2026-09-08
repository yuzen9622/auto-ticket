from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from domain.event import Event, EventCandidate, ResolveResult
from domain.preference import SeatPreference, TicketPreference
from domain.task import CreditCardProfile, UserContactProfile

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any


class TicketingAdapter(ABC):
    """Platform-specific purchase flow port (implemented from Batch 3 onward)."""

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
        self, page: Page, payment_profile: CreditCardProfile
    ) -> bool:
        """自動填寫信用卡卡號、過期日、CVV 並提交送出"""
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
