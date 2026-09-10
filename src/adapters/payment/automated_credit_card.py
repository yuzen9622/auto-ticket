"""真實刷卡 provider——**預設不可用**。

雙開關：建構參數 `allow_real_payment=True` **且** 環境變數
`AUTO_TICKET_ENABLE_REAL_PAYMENT=1`，任一缺席即拋 `RealPaymentNotEnabledError`。
兩個開關必須獨立（一個在程式碼、一個在執行環境），任何一邊被誤設都不足以刷卡。

PAN／CVV 只在記憶體中傳遞：不進 log、不進 Timeline、不進例外訊息、不進 `detail`。
`detail` 只放末四碼。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from adapters.payment.base import (
    PaymentOutcome,
    PaymentResult,
    RealPaymentNotEnabledError,
    masked_last4,
)
from adapters.ticketing.kktix.dom import first_visible, ng_click, ng_fill, page_text
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from domain.task import CreditCardProfile
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any

PROVIDER_NAME = "automated_credit_card"
REAL_PAYMENT_ENV_VAR = "AUTO_TICKET_ENABLE_REAL_PAYMENT"
REAL_PAYMENT_ENV_ENABLED = "1"

THREE_DS_URL_MARKERS = ("3ds", "acs", "securecode", "threeds")
THREE_DS_TEXT_MARKERS = ("3d secure", "簡訊驗證碼", "動態密碼")
DECLINED_TEXT_MARKERS = ("declined", "付款失敗", "交易失敗", "授權失敗")
THREE_DS_FAILED_TEXT_MARKERS = ("驗證失敗", "authentication failed")


def real_payment_enabled_in_env(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(REAL_PAYMENT_ENV_VAR) == REAL_PAYMENT_ENV_ENABLED


class AutomatedCreditCardProvider:
    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        allow_real_payment: bool = False,
        telemetry: TimelineRecorder | None = None,
        timeout_ms: int = 15000,
    ) -> None:
        if not allow_real_payment:
            raise RealPaymentNotEnabledError(
                "真實刷卡需要 allow_real_payment=True；預設一律使用 MockPaymentProvider"
            )
        if not real_payment_enabled_in_env():
            raise RealPaymentNotEnabledError(
                f"真實刷卡另需環境變數 {REAL_PAYMENT_ENV_VAR}={REAL_PAYMENT_ENV_ENABLED}"
            )
        self.telemetry = telemetry
        self.timeout_ms = timeout_ms

    async def pay(
        self, page: Page, profile: CreditCardProfile | None
    ) -> PaymentResult:
        if profile is None:
            return PaymentResult(
                outcome=PaymentOutcome.FAILED,
                provider=PROVIDER_NAME,
                detail={"reason": "missing_payment_profile", "submitted": False},
            )

        last4 = masked_last4(profile)
        radio = await self._locate(page, KKTIXSelectors.PAYMENT_RADIO_CREDIT_CARD, "payment_radio_credit_card")
        if radio is not None:
            await ng_click(page, radio)

        fields = (
            ("card_number", KKTIXSelectors.CARD_NUMBER_INPUT, profile.card_number),
            (
                "card_expiry",
                KKTIXSelectors.CARD_EXPIRY_INPUT,
                f"{profile.expiry_month}/{profile.expiry_year[-2:]}",
            ),
            ("card_security_code", KKTIXSelectors.CARD_CVV_INPUT, profile.cvv),
        )
        for field_name, selectors, value in fields:
            locator = await self._locate(page, selectors, field_name)
            if locator is None:
                return PaymentResult(
                    outcome=PaymentOutcome.FAILED,
                    provider=PROVIDER_NAME,
                    detail={"reason": f"field_not_found:{field_name}", "card_last4": last4, "submitted": False},
                )
            await ng_fill(page, locator, value)

        button = await self._locate(page, KKTIXSelectors.BTN_CONFIRM_PAYMENT, "btn_confirm_payment")
        if button is None:
            return PaymentResult(
                outcome=PaymentOutcome.FAILED,
                provider=PROVIDER_NAME,
                detail={"reason": "confirm_button_not_found", "card_last4": last4, "submitted": False},
            )

        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                "payment_submit",
                provider=PROVIDER_NAME,
                card_last4=last4,
            )
        await ng_click(page, button)

        try:
            await page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        except Exception:
            return PaymentResult(
                outcome=PaymentOutcome.TIMEOUT,
                provider=PROVIDER_NAME,
                detail={"reason": "load_state_timeout", "card_last4": last4, "submitted": True},
            )

        return self._classify(str(getattr(page, "url", "")), await page_text(page), last4)

    async def _locate(self, page: Page, selectors: Any, field_name: str) -> Any:
        return await first_visible(
            page,
            selectors,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
            field=field_name,
        )

    def _classify(self, url: str, text: str, last4: str) -> PaymentResult:
        lowered = f"{url}\n{text}".lower()
        detail = {"card_last4": last4, "submitted": True, "url": url}
        if any(m in lowered for m in THREE_DS_FAILED_TEXT_MARKERS):
            return PaymentResult(PaymentOutcome.THREE_DS_FAILED, PROVIDER_NAME, detail)
        if any(m in url.lower() for m in THREE_DS_URL_MARKERS) or any(
            m in lowered for m in THREE_DS_TEXT_MARKERS
        ):
            return PaymentResult(PaymentOutcome.THREE_DS_REQUIRED, PROVIDER_NAME, detail)
        if any(m in lowered for m in DECLINED_TEXT_MARKERS):
            return PaymentResult(PaymentOutcome.DECLINED, PROVIDER_NAME, detail)
        return PaymentResult(PaymentOutcome.SUBMITTED, PROVIDER_NAME, detail)
