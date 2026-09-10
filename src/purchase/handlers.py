"""購票步驟：adapter 結果 -> 合法 FSM 事件。

步驟函式只做三件事：呼叫 adapter、把結果翻成 FSM 事件、失敗時 `raise`。
**不得**自己吞錯，也**不得**自己中止排程——中止一律交給排程器既有的
fail-closed 路徑（handler 拋例外 -> 階段標記 FAILED -> abort -> FSM 抵達 FAILED）。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from adapters.ticketing.kktix.adapter import (
    REASON_NO_TICKET_UNITS,
    REASON_PLUS_BUTTON_MISSING,
    REASON_QUANTITY_MISMATCH,
    REASON_SELECTED,
    REASON_SOLD_OUT,
    REASON_TERMS_NOT_ACCEPTED,
)

EVENT_TICKET_RESERVED = "ticket_reserved"
EVENT_RETRY_FALLBACK_TICKET = "retry_fallback_ticket"
EVENT_ALL_TICKETS_UNAVAILABLE = "all_tickets_unavailable"
EVENT_SEAT_CONFIRMED = "seat_confirmed"
EVENT_SEAT_CONFLICT = "seat_conflict"
EVENT_FORM_SUBMITTED = "form_submitted"
EVENT_VERIFICATION_PASSED = "verification_passed"
EVENT_RETRY_VERIFICATION = "retry_verification"
EVENT_VERIFICATION_FAILED = "verification_failed"
EVENT_PAGE_LOADED = "page_loaded"
EVENT_SUBMIT_PAYMENT = "submit_payment"

# select_tickets 理由碼 -> FSM 事件；涵蓋 adapter 宣告的全部理由碼。
TICKET_REASON_EVENTS: Mapping[str, str] = MappingProxyType({
    REASON_SELECTED: EVENT_TICKET_RESERVED,
    REASON_SOLD_OUT: EVENT_ALL_TICKETS_UNAVAILABLE,
    REASON_NO_TICKET_UNITS: EVENT_RETRY_FALLBACK_TICKET,
    REASON_PLUS_BUTTON_MISSING: EVENT_RETRY_FALLBACK_TICKET,
    REASON_QUANTITY_MISMATCH: EVENT_RETRY_FALLBACK_TICKET,
    REASON_TERMS_NOT_ACCEPTED: EVENT_RETRY_FALLBACK_TICKET,
})


class PurchaseStepError(RuntimeError):
    """步驟無法繼續：交給排程器的 fail-closed 路徑處理。"""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step} failed: {reason}")
        self.step = step
        self.reason = reason


def ticket_event_for(reason: str) -> str:
    """未知理由碼一律視為可降級重試，而不是靜默當成成功。"""
    return TICKET_REASON_EVENTS.get(reason, EVENT_RETRY_FALLBACK_TICKET)


def seat_event_for(ok: bool) -> str:
    return EVENT_SEAT_CONFIRMED if ok else EVENT_SEAT_CONFLICT


def verification_event_for(solved: bool, can_retry: bool) -> str:
    if solved:
        return EVENT_VERIFICATION_PASSED
    return EVENT_RETRY_VERIFICATION if can_retry else EVENT_VERIFICATION_FAILED


def state_id_of(fsm: Any) -> str:
    return str(getattr(fsm, "current_state_id", ""))
