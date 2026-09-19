import {
  PURCHASE_FINAL_STATE,
  PURCHASE_STATE,
  type PurchaseState,
} from "@/lib/contract"

export type SemanticTone = "muted" | "accent" | "success" | "warning" | "danger"

/**
 * 狀態色映射。標籤不在這裡——所有面向使用者的文字一律走 i18n，
 * 這個模組只決定顏色語意。
 */
const PURCHASE_STATE_TONE: Record<PurchaseState, SemanticTone> = {
  IDLE: "muted",
  PREPARING: "muted",
  WAITING_FOR_SALE: "warning",
  SALE_OPEN: "accent",
  TICKET_SELECTION: "accent",
  SEAT_SELECTION: "accent",
  FORM_FILLING: "accent",
  VERIFICATION_REQUIRED: "warning",
  PAYMENT_REQUIRED: "warning",
  PAYMENT_PROCESSING: "accent",
  COMPLETED: "success",
  SOLD_OUT: "warning",
  TIMEOUT: "danger",
  FAILED: "danger",
}

const TASK_STATUS_TONE: Record<string, SemanticTone> = {
  CANCELLED: "muted",
  SCHEDULED: "accent",
  RUNNING: "success",
  PAUSED: "warning",
  COMPLETED: "success",
  FAILED: "danger",
}

const JOB_STATE_TONE: Record<string, SemanticTone> = {
  PENDING: "muted",
  CLAIMED: "accent",
  RUNNING: "success",
  DONE: "success",
  FAILED: "danger",
  CANCELLED: "muted",
}

export function purchaseStateTone(state: string): SemanticTone {
  return PURCHASE_STATE_TONE[state as PurchaseState] ?? "muted"
}

export function taskStatusTone(status: string): SemanticTone {
  return TASK_STATUS_TONE[status] ?? "muted"
}

export function jobStateTone(state: string): SemanticTone {
  return JOB_STATE_TONE[state] ?? "muted"
}

export function isFinalState(state: string): boolean {
  return (PURCHASE_FINAL_STATE as readonly string[]).includes(state)
}

export function isTaskFinished(status: string): boolean {
  return ["COMPLETED", "FAILED", "CANCELLED"].includes(status)
}

/** state-rail 的軌道順序：直接沿用後端 enum 宣告順序。 */
export const PURCHASE_STATE_ORDER: readonly PurchaseState[] = PURCHASE_STATE

export function purchaseStateIndex(state: string): number {
  return PURCHASE_STATE_ORDER.indexOf(state as PurchaseState)
}

export const TONE_TEXT_CLASS: Record<SemanticTone, string> = {
  muted: "text-muted-foreground",
  accent: "text-primary",
  success: "text-emerald-600 dark:text-emerald-400",
  warning: "text-amber-600 dark:text-amber-400",
  danger: "text-destructive",
}

export const TONE_DOT_CLASS: Record<SemanticTone, string> = {
  muted: "bg-muted-foreground",
  accent: "bg-primary",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-destructive",
}

export const TONE_BORDER_CLASS: Record<SemanticTone, string> = {
  muted: "border-border",
  accent: "border-primary",
  success: "border-emerald-500",
  warning: "border-amber-500",
  danger: "border-destructive",
}
