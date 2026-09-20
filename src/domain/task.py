from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Self

from pydantic import ConfigDict, Field, model_validator

from domain.execution import ExecutionMode
from domain.preference import TicketPreference
from domain.types import DomainBaseModel, UtcDatetime


class TaskStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StartTiming(str, Enum):
    """任務的兩種執行語意。

    `SCHEDULED`：活動還沒開賣，要等官方開賣時間，預熱必須在開賣前收工。
    `IMMEDIATE`：活動已經在販售，沒有任何東西要等——建立後直接開瀏覽器、進登記頁、
    讀票、下單。此時 `sale_start_at` 記的是任務建立時刻（本次執行的 T=0），
    不是活動的官方開賣時間，「開賣前幾秒必須收工」那一類規則一律不適用。
    """

    SCHEDULED = "scheduled"
    IMMEDIATE = "immediate"


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

    本規則只作用於主辦自訂的**文字**問答題。圖形驗證碼不走規則比對，改由 OCR provider 辨識（見 adapters/verification/ddddocr_provider.py）。
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
    start_timing: StartTiming = StartTiming.SCHEDULED
    """等開賣還是立即執行。舊 payload 沒有這個欄位時退回 `SCHEDULED`。"""
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

    # --- 自動化行為（一般設定）。舊 payload 沒有這些欄位時一律取預設值。 ---
    auto_cloudflare: bool = True
    """偵測到 Cloudflare 人機驗證時，先安靜等一段有上限的寬限期讓它自己過。

    關閉時維持「一偵測到就交還給人」的舊行為。本系統在任何情況下都不繞過 Cloudflare。"""
    auto_ocr: bool = True
    """圖片驗證碼交給 OCR 辨識。關閉時圖片題直接回報未解出，走人工路徑。"""
    auto_submit_verification: bool = True
    """辨識完成後自動送出。關閉時仍會把答案填好，但送出那一下由使用者自己按。"""

    # --- 進階設定 ---
    ocr_model_path: str | None = None
    """自訂 OCR 模型（.onnx）路徑；留空用內建模型。路徑無效時退回內建模型，不讓任務因此失敗。"""
    ocr_max_retries: int = Field(default=5, ge=1, le=20)
    """單次作答內「辨識 → 點刷新換圖 → 再辨識」的最大次數。與 FSM 的 max_retries 是兩回事：
    後者算的是送出後被判定答錯的重答次數。"""
    cloudflare_max_retries: int = Field(default=3, ge=0, le=20)
    """允許開啟幾次被動寬限窗；每次窗內會重複 probe 頁面直到通過或該次逾時。用罄後立即 fail-closed。"""
    debug_screenshots_and_logs: bool = False
    """每次 OCR 嘗試與每輪寬限 probe 額外截圖並記錄；排查用，平時關閉以免拖慢開賣瞬間。"""

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
