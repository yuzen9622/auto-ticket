from __future__ import annotations

from collections.abc import Mapping

from adapters.payment.automated_credit_card import (
    REAL_PAYMENT_ENV_VAR,
    AutomatedCreditCardProvider,
    real_payment_enabled_in_env,
)
from adapters.payment.base import (
    PaymentOutcome,
    PaymentProvider,
    PaymentResult,
    RealPaymentNotEnabledError,
    masked_last4,
)
from adapters.payment.manual_checkout import ManualCheckoutProvider
from adapters.payment.mock import (
    TEST_CARD_NUMBER,
    MockPaymentProvider,
)
from domain.execution import ExecutionMode, provider_allowed
from domain.task import CreditCardProfile
from telemetry.timeline import TimelineRecorder


def select_payment_provider(
    mode: ExecutionMode,
    *,
    telemetry: TimelineRecorder | None = None,
    payment_profile: CreditCardProfile | None = None,
    env: Mapping[str, str] | None = None,
) -> PaymentProvider:
    """依執行模式挑 provider——**唯一**的挑選入口。

    呼叫端只給模式，不給 adapter 名稱：外部輸入決定不了掛哪一顆 adapter。
    正式模式只有在卡片資料與環境開關都到位時才會真的刷卡，否則交回給真人結帳；
    無論哪一條路，回傳的 provider 都必須通過該模式的允許清單。
    """
    provider: PaymentProvider
    if mode is ExecutionMode.MOCK:
        provider = MockPaymentProvider(telemetry=telemetry)
    elif payment_profile is not None and real_payment_enabled_in_env(
        dict(env) if env is not None else None
    ):
        provider = AutomatedCreditCardProvider(
            allow_real_payment=True, telemetry=telemetry
        )
    else:
        provider = ManualCheckoutProvider(telemetry=telemetry)

    if not provider_allowed(mode, provider.name):
        raise RuntimeError(
            f"payment provider {provider.name!r} is not allowed for mode {mode.value!r}"
        )
    return provider


__all__ = [
    "REAL_PAYMENT_ENV_VAR",
    "TEST_CARD_NUMBER",
    "AutomatedCreditCardProvider",
    "ManualCheckoutProvider",
    "MockPaymentProvider",
    "PaymentOutcome",
    "PaymentProvider",
    "PaymentResult",
    "RealPaymentNotEnabledError",
    "masked_last4",
    "real_payment_enabled_in_env",
    "select_payment_provider",
]
