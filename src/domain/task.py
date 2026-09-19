from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Self

from pydantic import ConfigDict, Field, model_validator

from domain.execution import ExecutionMode
from domain.preference import TicketPreference
from domain.types import DomainBaseModel, UtcDatetime


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    SCHEDULED = "SCHEDULED"
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, Enum):
    MOCK = "mock"
    CREDIT_CARD = "credit_card"


class CreditCardProfile(DomainBaseModel):
    card_number: str = Field(pattern=r"^\d{13,19}$", repr=False)
    expiry_month: str = Field(pattern=r"^(0[1-9]|1[0-2])$")
    expiry_year: str = Field(pattern=r"^\d{2}$|^\d{4}$")
    cvv: str = Field(pattern=r"^\d{3,4}$", repr=False)
    cardholder_name: str = Field(min_length=1)

    def __repr__(self) -> str:
        return f"CreditCardProfile(card_number='****{self.card_number[-4:]}')"

    def __str__(self) -> str:
        return self.__repr__()


class UserContactProfile(DomainBaseModel):
    name: str = Field(min_length=1)
    phone: str = Field(pattern=r"^[0-9+\-() ]{8,20}$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AttendeeProfile(DomainBaseModel):
    """實名制活動的個別參加人資料。

    KKTIX 的實名制活動會為每張票要求一組 `attendees[i]` 欄位；數量由頁面決定，
    不得臆造。`id_number` 只在活動確實索取時提供。
    """

    name: str = Field(min_length=1)
    phone: str = Field(pattern=r"^[0-9+\-() ]{8,20}$")
    id_number: str | None = None


class VerificationRule(DomainBaseModel):
    """文字問答題的一條作答規則。

    只處理主辦自訂的**文字**問答題；本專案不辨識任何圖形驗證碼。
    `is_regex` 為 False 時 `pattern` 以子字串比對題幹（不分大小寫）。
    """

    pattern: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    is_regex: bool = False


class PurchaseTaskSpec(DomainBaseModel):
    model_config = ConfigDict(
        validate_assignment=True,
        extra="forbid",
        frozen=True,
    )

    task_id: str
    event_title: str
    event_url: str
    sale_start_at: UtcDatetime
    ticket_preference: TicketPreference
    contact_profile: UserContactProfile
    attendees: tuple[AttendeeProfile, ...] = ()
    execution_mode: ExecutionMode = ExecutionMode.MOCK
    """決定 Worker 掛哪一顆付款 adapter；舊 payload 沒有這個欄位時退回最保守的 MOCK。"""
    payment_method: PaymentMethod = PaymentMethod.CREDIT_CARD
    payment_profile: CreditCardProfile | None = None
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=120, gt=0)
    verification_rules: tuple[VerificationRule, ...] = ()
    auto_login: bool = False
    qualification_code: str | None = None
    session_preference: str | None = None
    """多場次活動要買哪一場（比對場次標籤的子字串，例如「上午場」）。

    母活動的登記頁沒有票種，不先選場次就永遠看不到票。留空時只有單場次活動能繼續，
    多場次會 fail-closed——買錯場次不可逆，不替使用者猜。"""

    @model_validator(mode="after")
    def _require_payment_profile(self) -> PurchaseTaskSpec:
        if (
            self.payment_method is PaymentMethod.CREDIT_CARD
            and self.payment_profile is None
        ):
            raise ValueError("credit_card payment requires payment_profile")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        if update:
            return self.model_validate(dict(copied.__dict__))
        return copied

    def to_persistable_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"payment_profile"})


class PurchaseTaskRecord(DomainBaseModel):
    id: str
    event_id: str | None = None
    status: TaskStatus
    spec: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: UtcDatetime | None = None
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None
    created_at: UtcDatetime
