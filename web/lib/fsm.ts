import {
  PURCHASE_FINAL_STATE,
  PURCHASE_STATE,
  type PurchaseState,
} from "@/lib/contract"

export type SemanticTone = "muted" | "accent" | "success" | "warning" | "danger"

/** 計畫 §4.4 狀態色映射。 */
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

const PURCHASE_STATE_LABEL: Record<PurchaseState, string> = {
  IDLE: "閒置",
  PREPARING: "準備中",
  WAITING_FOR_SALE: "等待開賣",
  SALE_OPEN: "開賣",
  TICKET_SELECTION: "選票種",
  SEAT_SELECTION: "選座位",
  FORM_FILLING: "填表",
  VERIFICATION_REQUIRED: "需驗證",
  PAYMENT_REQUIRED: "需付款",
  PAYMENT_PROCESSING: "付款中",
  COMPLETED: "完成",
  SOLD_OUT: "售罄",
  TIMEOUT: "逾時",
  FAILED: "失敗",
}

const TASK_STATUS_TONE: Record<string, SemanticTone> = {
  CREATED: "muted",
  CANCELLED: "muted",
  SCHEDULED: "accent",
  PREPARING: "accent",
  READY: "accent",
  RUNNING: "success",
  PAUSED: "warning",
  COMPLETED: "success",
  FAILED: "danger",
}

const TASK_STATUS_LABEL: Record<string, string> = {
  CREATED: "已建立",
  SCHEDULED: "已排程",
  PREPARING: "準備中",
  READY: "就緒",
  RUNNING: "執行中",
  PAUSED: "已暫停",
  COMPLETED: "已完成",
  FAILED: "失敗",
  CANCELLED: "已取消",
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

export function purchaseStateLabel(state: string): string {
  return PURCHASE_STATE_LABEL[state as PurchaseState] ?? state
}

export function taskStatusTone(status: string): SemanticTone {
  return TASK_STATUS_TONE[status] ?? "muted"
}

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABEL[status] ?? status
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
  muted: "text-[var(--oc-muted)]",
  accent: "text-[var(--oc-accent)]",
  success: "text-[var(--oc-success)]",
  warning: "text-[var(--oc-warning)]",
  danger: "text-[var(--oc-danger)]",
}

export const TONE_DOT_CLASS: Record<SemanticTone, string> = {
  muted: "bg-[var(--oc-muted)]",
  accent: "bg-[var(--oc-accent)]",
  success: "bg-[var(--oc-success)]",
  warning: "bg-[var(--oc-warning)]",
  danger: "bg-[var(--oc-danger)]",
}

export const TONE_BORDER_CLASS: Record<SemanticTone, string> = {
  muted: "border-[var(--oc-border)]",
  accent: "border-[var(--oc-accent)]",
  success: "border-[var(--oc-success)]",
  warning: "border-[var(--oc-warning)]",
  danger: "border-[var(--oc-danger)]",
}
