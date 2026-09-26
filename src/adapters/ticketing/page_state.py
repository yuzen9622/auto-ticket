"""平台中立的頁面狀態、理由碼與挑戰例外。

提供所有票券平台 adapter 與協調器共用的枚舉、常數與例外。
"""

from __future__ import annotations

from enum import Enum

# select_tickets 的理由碼；協調器依此對應 FSM 事件。
REASON_SELECTED = "SELECTED"
REASON_SOLD_OUT = "SOLD_OUT"
REASON_NO_TICKET_UNITS = "NO_TICKET_UNITS"
REASON_PLUS_BUTTON_MISSING = "PLUS_BUTTON_MISSING"
REASON_QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
REASON_TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
REASON_CLOUDFLARE = "CLOUDFLARE_CHALLENGE"
REASON_NOT_REGISTRATION_PAGE = "NOT_REGISTRATION_PAGE"
REASON_VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"

ALL_TICKET_REASONS = frozenset(
    {
        REASON_SELECTED,
        REASON_SOLD_OUT,
        REASON_NO_TICKET_UNITS,
        REASON_PLUS_BUTTON_MISSING,
        REASON_QUANTITY_MISMATCH,
        REASON_TERMS_NOT_ACCEPTED,
        REASON_NOT_REGISTRATION_PAGE,
    }
)

CLOUDFLARE_MARK = "cloudflare_challenge"
FAILURE_MODAL_MARK = "failure_modal_dismissed"
FAILURE_MODAL_MISSING_MARK = "failure_modal_dismiss_missing"
RESET_MARK = "ticket_quantities_reset"

RESET_REASON_NO_QUANTITY_FIELD = "NO_QUANTITY_FIELD"
RESET_REASON_MINUS_MISSING = "MINUS_BUTTON_MISSING"
RESET_REASON_UNREADABLE = "QUANTITY_UNREADABLE"
RESET_REASON_NOT_ZERO = "QUANTITY_NOT_ZERO"
ZERO_QUANTITY = "0"


class PageKind(str, Enum):
    """目前停在哪一種頁面。票種的讀法在不同頁面上不同，不得混用。"""

    EVENT = "EVENT"
    """公開活動主頁：票種是一張表格，只看得到售賣時段，看不到可購買數量。"""
    REGISTRATION = "REGISTRATION"
    """購票登記頁：票種是可加減數量的單元，這裡才下得了單。"""
    ORDER = "ORDER"
    """劃位與訂單填寫頁：已經有訂單了，這裡填聯絡人與參加人。"""
    LOGIN = "LOGIN"
    """被導到登入頁：需要人自己登入，或由 session handler 自動登入。"""
    CHALLENGE = "CHALLENGE"
    """人機驗證挑戰頁。"""
    UNKNOWN = "UNKNOWN"
    """以上皆非——多半是被導去登入頁或錯誤頁。"""


# 相容別名
KKTIXPageKind = PageKind


class PageState(str, Enum):
    """搶票迴圈每一輪實際看到的頁面狀態。

    與 `PageKind`（「這是哪一類網址」）刻意分離：這裡描述的是「現在該做哪個
    動作」，粒度細到彈窗與局部區塊。**絕不**包含 `SOLD_OUT`——售罄是策略層看完
    票種快照後的結論，不是頁面上讀得到的物理特徵。
    """

    FAILURE_MODAL = "FAILURE_MODAL"
    """「別人搶先一步」「已無可配座位」——這一輪搶輸了，得換票種。"""
    GUEST_MODAL = "GUEST_MODAL"
    """未登入訪客彈窗：勸你先成為會員。"""
    QUEUE = "QUEUE"
    """排隊等候室。**嚴禁 reload**：重整會被丟回隊伍尾端。"""
    COMPLETED = "COMPLETED"
    """訂單完成頁（未付款保留訂單亦算抵達）。"""
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    """付款頁。"""
    QUALIFICATION_CODE = "QUALIFICATION_CODE"
    """專屬會員碼／邀請碼的資格審查區塊。"""
    FORM_FILLING = "FORM_FILLING"
    """聯絡人與動態同意條款表單；問答題同屬這張表單，故優先於局部驗證題。"""
    VERIFICATION_CHALLENGE = "VERIFICATION_CHALLENGE"
    """只有題幹與答案欄、沒有表單欄位的獨立驗證題。"""
    SEAT_SELECTION = "SEAT_SELECTION"
    """登記頁且**已選數量 > 0**：該按配位或下一步了。"""
    TICKET_SELECTION = "TICKET_SELECTION"
    """登記頁且**已選數量 == 0**：還沒選票。"""
    UNKNOWN = "UNKNOWN"
    """過渡暫態。呼叫端只能微等待後重判，逾時即 fail-closed。"""


class CloudflareChallengeError(RuntimeError):
    """偵測到人機驗證挑戰；一律 fail-closed 中止，不嘗試繞過。"""


class LoginState(str, Enum):
    """登入狀態的三種可能，刻意不把「不知道」摺進「未登入」。"""

    LOGGED_IN = "LOGGED_IN"
    LOGGED_OUT = "LOGGED_OUT"
    UNKNOWN = "UNKNOWN"
