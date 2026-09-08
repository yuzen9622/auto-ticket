from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Self

from pydantic import ConfigDict, Field, model_validator

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
    payment_method: PaymentMethod = PaymentMethod.CREDIT_CARD
    payment_profile: CreditCardProfile | None = None
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=120, gt=0)

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
