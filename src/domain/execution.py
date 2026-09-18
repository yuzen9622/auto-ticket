from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    """任務要用哪一種方式執行。

    `LIVE` 走真實購票流程（真實帳號、真實票券、真實訂單）；`MOCK` 只供開發與測試，
    付款一定停在 checkpoint。模式是後端權威：前端只送模式名稱，實際掛哪一顆 adapter
    由 `ALLOWED_PAYMENT_PROVIDERS` 決定，前端送進來的 adapter 名稱一律不採信。
    """

    LIVE = "live"
    MOCK = "mock"


#: 每個模式允許使用的付款 provider 名稱（`PaymentProvider.name`）。
#: 任何不在此清單內的 provider 都不得掛給該模式。
ALLOWED_PAYMENT_PROVIDERS: dict[ExecutionMode, tuple[str, ...]] = {
    ExecutionMode.LIVE: ("automated_credit_card", "manual_checkout"),
    ExecutionMode.MOCK: ("mock",),
}


def coerce_execution_mode(value: object) -> ExecutionMode:
    """把任意輸入收斂成合法模式；無法辨識時退回最保守的 `MOCK`。

    用於讀取歷史 payload——舊資料沒有這個欄位，硬解析會讓 Worker 在啟動時就炸掉。
    """
    if isinstance(value, ExecutionMode):
        return value
    try:
        return ExecutionMode(str(value).strip().lower())
    except ValueError:
        return ExecutionMode.MOCK


def provider_allowed(mode: ExecutionMode, provider_name: str) -> bool:
    return provider_name in ALLOWED_PAYMENT_PROVIDERS.get(mode, ())
