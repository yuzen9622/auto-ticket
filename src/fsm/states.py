from __future__ import annotations

from enum import Enum


class PurchaseState(str, Enum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    WAITING_FOR_SALE = "WAITING_FOR_SALE"
    SALE_OPEN = "SALE_OPEN"
    TICKET_SELECTION = "TICKET_SELECTION"
    SEAT_SELECTION = "SEAT_SELECTION"
    FORM_FILLING = "FORM_FILLING"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    PAYMENT_PROCESSING = "PAYMENT_PROCESSING"
    COMPLETED = "COMPLETED"
    SOLD_OUT = "SOLD_OUT"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


class PurchaseEvent(str, Enum):
    prepare_session = "prepare_session"
    session_ready = "session_ready"
    sale_triggered = "sale_triggered"
    page_loaded = "page_loaded"
    ticket_reserved = "ticket_reserved"
    retry_fallback_ticket = "retry_fallback_ticket"
    all_tickets_unavailable = "all_tickets_unavailable"
    seat_confirmed = "seat_confirmed"
    seat_conflict = "seat_conflict"
    form_submitted = "form_submitted"
    verification_passed = "verification_passed"
    retry_verification = "retry_verification"
    verification_failed = "verification_failed"
    submit_payment = "submit_payment"
    payment_success = "payment_success"
    payment_declined = "payment_declined"
    abort_timeout = "abort_timeout"
    abort_failed = "abort_failed"


FINAL_STATES = frozenset({
    PurchaseState.COMPLETED,
    PurchaseState.SOLD_OUT,
    PurchaseState.TIMEOUT,
    PurchaseState.FAILED,
})
