/**
 * 後端 enum 的前端鏡像 —— 這裡是前端的唯一真實來源。
 * 由 `scripts/check-contract.mjs` 對 Python 原始碼做集合相等比對，漂移即 CI 紅燈。
 */

/** src/domain/task.py TaskStatus */
export const TASK_STATUS = [
  "CREATED",
  "SCHEDULED",
  "PREPARING",
  "READY",
  "RUNNING",
  "PAUSED",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
] as const
export type TaskStatus = (typeof TASK_STATUS)[number]

/** src/fsm/states.py PurchaseState */
export const PURCHASE_STATE = [
  "IDLE",
  "PREPARING",
  "WAITING_FOR_SALE",
  "SALE_OPEN",
  "TICKET_SELECTION",
  "SEAT_SELECTION",
  "FORM_FILLING",
  "VERIFICATION_REQUIRED",
  "PAYMENT_REQUIRED",
  "PAYMENT_PROCESSING",
  "COMPLETED",
  "SOLD_OUT",
  "TIMEOUT",
  "FAILED",
] as const
export type PurchaseState = (typeof PURCHASE_STATE)[number]

/** src/fsm/states.py FINAL_STATES */
export const PURCHASE_FINAL_STATE = [
  "COMPLETED",
  "SOLD_OUT",
  "TIMEOUT",
  "FAILED",
] as const
export type PurchaseFinalState = (typeof PURCHASE_FINAL_STATE)[number]

/** src/fsm/states.py PurchaseEvent */
export const PURCHASE_EVENT = [
  "prepare_session",
  "session_ready",
  "sale_triggered",
  "page_loaded",
  "ticket_reserved",
  "retry_fallback_ticket",
  "all_tickets_unavailable",
  "seat_confirmed",
  "seat_conflict",
  "form_submitted",
  "verification_passed",
  "retry_verification",
  "verification_failed",
  "submit_payment",
  "payment_success",
  "payment_declined",
  "abort_timeout",
  "abort_failed",
] as const
export type PurchaseEvent = (typeof PURCHASE_EVENT)[number]

/** src/broker/jobs.py JobState */
export const JOB_STATE = [
  "PENDING",
  "CLAIMED",
  "RUNNING",
  "DONE",
  "FAILED",
  "CANCELLED",
] as const
export type JobState = (typeof JOB_STATE)[number]

/** src/api/schemas/ws.py ServerMessageType */
export const SERVER_MESSAGE_TYPE = [
  "STATE_CHANGED",
  "CLOCK_TICK",
  "SCREENSHOT_CAPTURED",
  "TASK_LOG",
  "ERROR",
] as const
export type ServerMessageType = (typeof SERVER_MESSAGE_TYPE)[number]

/** src/api/schemas/ws.py ClientAction */
export const CLIENT_ACTION = [
  "EMERGENCY_STOP",
  "PAUSE",
  "RESUME",
  "FORCE_TRANSITION",
] as const
export type ClientAction = (typeof CLIENT_ACTION)[number]

/** src/api/errors.py CODE_* */
export const API_ERROR_CODE = [
  "not_found",
  "invalid_request",
  "conflict",
  "upstream_failed",
  "vault_locked",
  "unsupported",
] as const
export type ApiErrorCode = (typeof API_ERROR_CODE)[number]

/** 後端 domain 驗證 regex，逐字複製自 src/domain/task.py，避免無謂的 422 往返。 */
export const PHONE_PATTERN = /^[0-9+\-() ]{8,20}$/
export const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/** 僅 kktix 有 resolver；另兩值保留在契約內但不出現在選單（計畫 §未決事項 3）。 */
export const PLATFORM = ["kktix", "tixcraft", "ibon"] as const
export type Platform = (typeof PLATFORM)[number]

export const SEAT_STRATEGY = [
  "best_available",
  "same_zone",
  "specific_zone",
] as const
export type SeatStrategy = (typeof SEAT_STRATEGY)[number]
