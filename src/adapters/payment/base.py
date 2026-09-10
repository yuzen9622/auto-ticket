"""付款 Port。

**[FROZEN-3][PAYMENT-MOCK-DEFAULT]**：`MockPaymentProvider` 是唯一預設；真實刷卡
必須同時滿足建構參數與環境變數兩個獨立開關。PAN／CVV 永不落地——不得寫入 SQLite、
不得出現在 structlog 輸出、`TimelineEvent.detail`、截圖檔名或例外訊息。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from domain.task import CreditCardProfile

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any


class PaymentOutcome(str, Enum):
    SUBMITTED = "SUBMITTED"
    """真的送出了付款（只有 Automated provider 可能回傳）。"""
    CHECKPOINT_REACHED = "CHECKPOINT_REACHED"
    """Mock：抵達送出鈕之前即停止，未發動任何金流。"""
    DECLINED = "DECLINED"
    THREE_DS_REQUIRED = "THREE_DS_REQUIRED"
    THREE_DS_FAILED = "THREE_DS_FAILED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


class RealPaymentNotEnabledError(RuntimeError):
    """真實刷卡的雙開關未同時滿足。"""


@dataclass(frozen=True, slots=True)
class PaymentResult:
    outcome: PaymentOutcome
    provider: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    """**嚴禁**含 PAN／CVV；卡片資訊一律只放末四碼。"""

    @property
    def submitted(self) -> bool:
        return self.outcome is PaymentOutcome.SUBMITTED


@runtime_checkable
class PaymentProvider(Protocol):
    name: str

    async def pay(
        self, page: Page, profile: CreditCardProfile | None
    ) -> PaymentResult: ...


def masked_last4(profile: CreditCardProfile | None) -> str:
    """可安全記錄的卡片識別：只有末四碼。"""
    if profile is None:
        return "none"
    return profile.card_number[-4:]
