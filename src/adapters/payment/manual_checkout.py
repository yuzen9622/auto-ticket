"""正式模式的預設付款 provider：把付款交回給真人完成。

`MockPaymentProvider` 會把公開測試卡號填進表單——那在**正式**訂單上是有害的，
所以正式模式不能借用它。本 provider 只做一件安全的事：把付款方式切到信用卡分頁，
然後停下來，回報 `CHECKPOINT_REACHED` 並標記 `requires_manual_completion`。
不填任何卡號、不點送出鈕、不讀取 `CreditCardProfile`。

真正的自動刷卡走 `AutomatedCreditCardProvider`，它另有兩道獨立開關。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adapters.payment.base import PaymentOutcome, PaymentResult
from adapters.ticketing.kktix.dom import (
    DEFAULT_OPTIONAL_PROBE_MS,
    first_visible,
    ng_click,
)
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from domain.task import CreditCardProfile
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any

PROVIDER_NAME = "manual_checkout"
HANDOFF_MARK = "payment_handed_to_operator"


class ManualCheckoutProvider:
    """停在付款頁、等待真人完成結帳的 provider。"""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        telemetry: TimelineRecorder | None = None,
        timeout_ms: int = 2000,
        probe_timeout_ms: int | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.timeout_ms = timeout_ms
        self.probe_timeout_ms = min(
            DEFAULT_OPTIONAL_PROBE_MS if probe_timeout_ms is None else probe_timeout_ms,
            timeout_ms,
        )

    async def pay(self, page: Page, profile: CreditCardProfile | None) -> PaymentResult:
        # profile 刻意不讀：正式模式的卡片資料不經過本程式。
        del profile

        radio = await first_visible(
            page,
            KKTIXSelectors.PAYMENT_RADIO_CREDIT_CARD,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="payment_radio_credit_card",
        )
        if radio is not None:
            await ng_click(page, radio, telemetry=self.telemetry)

        # 到此為止。**嚴禁**定位或點擊 KKTIXSelectors.BTN_CONFIRM_PAYMENT。
        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                HANDOFF_MARK,
                provider=PROVIDER_NAME,
                requires_manual_completion=True,
            )

        return PaymentResult(
            outcome=PaymentOutcome.CHECKPOINT_REACHED,
            provider=PROVIDER_NAME,
            detail={
                "submitted": False,
                "requires_manual_completion": True,
                "used_test_card": False,
                "real_profile_read": False,
            },
        )
