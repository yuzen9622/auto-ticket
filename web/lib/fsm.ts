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

export type PurchasePhaseKey =
  | "preparing"
  | "waitingForSale"
  | "picking"
  | "checkout"
  | "finished"

/**
 * 進度階段：把 14 個 PurchaseState 收成使用者真的在等的幾個里程碑。
 *
 * 細分狀態（選票種／選座位、填表／驗證／付款）對執行程式有意義，對看畫面的人
 * 只是雜訊；他們要知道的是「現在到哪一步了」。狀態本身仍完整走 FSM，這裡只影響顯示。
 */
export const PURCHASE_PHASES: readonly {
  key: PurchasePhaseKey
  states: readonly PurchaseState[]
}[] = [
  { key: "preparing", states: ["IDLE", "PREPARING"] },
  { key: "waitingForSale", states: ["WAITING_FOR_SALE"] },
  {
    key: "picking",
    states: ["SALE_OPEN", "TICKET_SELECTION", "SEAT_SELECTION"],
  },
  {
    key: "checkout",
    states: [
      "FORM_FILLING",
      "VERIFICATION_REQUIRED",
      "PAYMENT_REQUIRED",
      "PAYMENT_PROCESSING",
    ],
  },
  { key: "finished", states: ["COMPLETED", "SOLD_OUT", "TIMEOUT", "FAILED"] },
]

/** 該狀態落在第幾個階段；認不得的狀態回 -1。 */
export function purchasePhaseIndex(state: string | null): number {
  if (!state) return -1
  return PURCHASE_PHASES.findIndex((phase) =>
    (phase.states as readonly string[]).includes(state)
  )
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
