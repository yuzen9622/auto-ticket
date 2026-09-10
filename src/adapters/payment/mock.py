"""預設付款 provider：**絕不發動金流**。

契約：填入固定測試卡號字串（不從 `CreditCardProfile` 讀真卡），
在「確認付款」按鈕**之前**停住並回報 `CHECKPOINT_REACHED`。
`simulate` 只是把研究用的假結果餵給狀態機，不改變「不送出」這件事。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adapters.payment.base import PaymentOutcome, PaymentResult
from adapters.ticketing.kktix.dom import first_visible, ng_click, ng_fill
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from domain.task import CreditCardProfile
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any

PROVIDER_NAME = "mock"
# 公開測試卡號（Stripe/各家共用的 sandbox 號碼），與使用者的真卡完全無關。
TEST_CARD_NUMBER = "4242424242424242"
TEST_CARD_EXPIRY = "12/34"
TEST_CARD_SECURITY_CODE = "123"
CHECKPOINT_MARK = "payment_checkpoint_reached"


class MockPaymentProvider:
    """填到付款表單為止就停手的 provider。"""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        simulate: PaymentOutcome = PaymentOutcome.CHECKPOINT_REACHED,
        telemetry: TimelineRecorder | None = None,
        timeout_ms: int = 2000,
    ) -> None:
        self.simulate = simulate
        self.telemetry = telemetry
        self.timeout_ms = timeout_ms

    async def pay(
        self, page: Page, profile: CreditCardProfile | None
    ) -> PaymentResult:
        filled: list[str] = []

        radio = await first_visible(
            page,
            KKTIXSelectors.PAYMENT_RADIO_CREDIT_CARD,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
            field="payment_radio_credit_card",
        )
        if radio is not None:
            await ng_click(page, radio, telemetry=self.telemetry)

        for field_name, selectors, value in (
            ("card_number", KKTIXSelectors.CARD_NUMBER_INPUT, TEST_CARD_NUMBER),
            ("card_expiry", KKTIXSelectors.CARD_EXPIRY_INPUT, TEST_CARD_EXPIRY),
            ("card_security_code", KKTIXSelectors.CARD_CVV_INPUT, TEST_CARD_SECURITY_CODE),
        ):
            locator = await first_visible(
                page,
                selectors,
                timeout_ms=self.timeout_ms,
                telemetry=self.telemetry,
                field=field_name,
            )
            if locator is None:
                continue
            await ng_fill(page, locator, value)
            filled.append(field_name)

        # 到此為止。**嚴禁**定位或點擊 KKTIXSelectors.BTN_CONFIRM_PAYMENT。
        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                CHECKPOINT_MARK,
                provider=PROVIDER_NAME,
                simulate=self.simulate.value,
                filled_fields=tuple(filled),
            )
        return PaymentResult(
            outcome=self.simulate,
            provider=PROVIDER_NAME,
            detail={
                "submitted": False,
                "simulated": self.simulate.value,
                "filled_fields": tuple(filled),
                "used_test_card": True,
                "real_profile_read": False,
            },
        )
