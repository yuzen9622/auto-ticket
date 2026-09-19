import type { SemanticTone } from "@/lib/fsm"

export type WsStatus = "connecting" | "open" | "reconnecting" | "closed"

/**
 * 頁面狀態 = TaskStatus × WS 連線狀態的衍生投影（計畫 §3.4）。
 * 純顯示層，不落資料庫、不進 API，也不是後端的第五套 enum。
 */
export type PageState =
  "LIVE" | "SCHEDULED" | "PAUSED" | "FINISHED" | "UNKNOWN"

export function derivePageState(
  taskStatus: string | null | undefined,
  wsStatus: WsStatus
): PageState {
  if (taskStatus === "RUNNING" && wsStatus === "open") return "LIVE"
  if (taskStatus === "SCHEDULED") return "SCHEDULED"
  if (taskStatus === "PAUSED") return "PAUSED"
  if (
    taskStatus === "COMPLETED" ||
    taskStatus === "FAILED" ||
    taskStatus === "CANCELLED"
  ) {
    return "FINISHED"
  }
  return "UNKNOWN"
}

export const PAGE_STATE_TONE: Record<PageState, SemanticTone> = {
  LIVE: "success",
  SCHEDULED: "accent",
  PAUSED: "warning",
  FINISHED: "muted",
  UNKNOWN: "muted",
}

export const WS_STATUS_TONE: Record<WsStatus, SemanticTone> = {
  connecting: "warning",
  open: "success",
  reconnecting: "warning",
  closed: "muted",
}
