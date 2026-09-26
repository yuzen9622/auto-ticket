from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from adapters.ticketing.page_state import LoginState, PageKind, PageState
from domain.event import Event, EventCandidate, PlatformEnum, ResolveResult
from domain.preference import SeatPreference, TicketPreference
from domain.task import CreditCardProfile, UserContactProfile
from strategy.ticket_strategy import TicketDecision, TicketOption

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

    platform: ClassVar[PlatformEnum]

    def __init__(self, *, ticket_preference: TicketPreference | None = None) -> None:
        self.ticket_preference = ticket_preference
        self._target_quantity: int | None = (
            ticket_preference.quantity if ticket_preference is not None else None
        )

    @abstractmethod
    async def navigate_to_event(
        self, page: Page, event_url: str, session_preference: str | None = None
    ) -> bool:
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

    @abstractmethod
    async def probe_page(self, page: Page, url: str | None = None) -> PageKind:
        """探測當前頁面類型（主頁、登記頁、訂單頁、登入頁等）"""
        raise NotImplementedError

    @abstractmethod
    async def detect_page_state(self, page: Page) -> PageState:
        """搶票迴圈每一輪實際看到的頁面狀態"""
        raise NotImplementedError

    @abstractmethod
    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        """讀取當前登記頁面上的票種資訊"""
        raise NotImplementedError

    @abstractmethod
    async def apply_ticket_decision(
        self, page: Page, decision: TicketDecision
    ) -> tuple[bool, str]:
        """把已經作出的票種決策套用到頁面上"""
        raise NotImplementedError

    @abstractmethod
    async def reset_ticket_quantities(self, page: Page) -> bool:
        """將已選票數全部歸零"""
        raise NotImplementedError

    @abstractmethod
    async def dismiss_failure_modal(self, page: Page) -> bool:
        """關閉搶票失敗彈窗（如「別人搶先一步」）"""
        raise NotImplementedError

    @abstractmethod
    async def detect_verification(self, page: Page) -> bool:
        """偵測頁面是否要求驗證題"""
        raise NotImplementedError

    @abstractmethod
    async def submit_order(self, page: Page) -> bool:
        """送出訂單（確認表單）"""
        raise NotImplementedError

    @abstractmethod
    async def login(self, page: Page, username: str, secret_token: str) -> bool:
        """以帳號密碼登入"""
        raise NotImplementedError

    @abstractmethod
    async def navigate_to_login_from_guest_modal(self, page: Page) -> bool:
        """從未登入彈窗導航至登入頁"""
        raise NotImplementedError

    @abstractmethod
    async def submit_qualification_code(self, page: Page, code: str) -> bool:
        """填入並送出特權碼／資格碼"""
        raise NotImplementedError

    @abstractmethod
    async def detect_verification_error(self, page: Page) -> bool:
        """偵測驗證題作答是否有錯誤提示"""
        raise NotImplementedError

    @abstractmethod
    async def handle_cloudflare(self, page: Page) -> bool:
        """單次探測並嘗試解決 Cloudflare / Turnstile 挑戰"""
        raise NotImplementedError

    @abstractmethod
    async def probe_login_state(self, page: Page) -> LoginState:
        """探測當前頁面與 Cookie 的登入狀態（LOGGED_IN / LOGGED_OUT / UNKNOWN）。"""
        raise NotImplementedError


class ResolveError(Exception):
    """Event resolver 基礎例外。"""


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
