import type { SemanticTone } from "@/lib/fsm"

export type WsStatus = "connecting" | "open" | "reconnecting" | "closed"

/**
 * 頁面狀態 = TaskStatus × WS 連線狀態的衍生投影（計畫 §3.4）。
 * 純顯示層，不落資料庫、不進 API，也不是後端的第五套 enum。
 */
export type PageState =
  "LIVE" | "SCHEDULED" | "WARMING_UP" | "PAUSED" | "FINISHED" | "UNKNOWN"

export function derivePageState(
  taskStatus: string | null | undefined,
  wsStatus: WsStatus
): PageState {
  if (taskStatus === "RUNNING" && wsStatus === "open") return "LIVE"
  if (taskStatus === "CREATED" || taskStatus === "SCHEDULED") return "SCHEDULED"
  if (taskStatus === "PREPARING" || taskStatus === "READY") return "WARMING_UP"
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

export const PAGE_STATE_LABEL: Record<PageState, string> = {
  LIVE: "即時連線",
  SCHEDULED: "已排程",
  WARMING_UP: "預熱中",
  PAUSED: "已暫停",
  FINISHED: "已結束",
  UNKNOWN: "未知",
}

export const PAGE_STATE_TONE: Record<PageState, SemanticTone> = {
  LIVE: "success",
  SCHEDULED: "accent",
  WARMING_UP: "accent",
  PAUSED: "warning",
  FINISHED: "muted",
  UNKNOWN: "muted",
}

export const WS_STATUS_LABEL: Record<WsStatus, string> = {
  connecting: "連線中",
  open: "已連線",
  reconnecting: "重連中",
  closed: "連線已結束",
}

export const WS_STATUS_TONE: Record<WsStatus, SemanticTone> = {
  connecting: "warning",
  open: "success",
  reconnecting: "warning",
  closed: "muted",
}
