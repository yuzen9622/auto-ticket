import { formatTimeOfDay, parseServerDate } from "@/lib/format"
import type { ServerMessage } from "@/lib/ws/types"
import { isAck, isHumanGate, isProtocolError, isSnapshot } from "@/lib/ws/types"

export type LogLevel = "error" | "state" | "shot" | "tick" | "info" | "snap"

export interface LogEntry {
  key: string
  level: LogLevel
  time: string
  summary: string
  raw: ServerMessage
}

/** 環形緩衝硬上限：CLOCK_TICK 已被排除，2000 筆足以涵蓋整場購票。 */
export const LOG_BUFFER_LIMIT = 2000

/** LEVEL 欄固定 5 字元寬，維持等寬對齊。 */
export const LEVEL_WIDTH = 5

export function levelLabel(level: LogLevel): string {
  return level.toUpperCase().padEnd(LEVEL_WIDTH).slice(0, LEVEL_WIDTH)
}

export function deriveLevel(msg: ServerMessage): LogLevel {
  switch (msg.type) {
    case "ERROR":
      return "error"
    case "STATE_CHANGED":
      return "state"
    case "SCREENSHOT_CAPTURED":
      return "shot"
    case "CLOCK_TICK":
      return "tick"
    case "TASK_LOG": {
      const p = msg.payload
      if (isSnapshot(p)) return "snap"
      // 等人處理是要被看見的事，歸在最顯眼的等級，不要沉進一般訊息裡。
      if (isHumanGate(p)) return "error"
      if (!isAck(p) && !isHumanGate(p) && p.event_type === "error") return "error"
      return "info"
    }
  }
}

export function entryKey(msg: ServerMessage, seq: number): string {
  if (msg.outbox_id !== null && msg.outbox_id !== undefined) {
    return `o${msg.outbox_id}`
  }
  return `${msg.timestamp}|${msg.type}|${seq}`
}

export function summarize(msg: ServerMessage): string {
  switch (msg.type) {
    case "STATE_CHANGED": {
      const p = msg.payload
      return `${p.from_state} → ${p.to_state}  (${p.event}, ${Math.round(p.elapsed_ms)}ms)`
    }
    case "CLOCK_TICK": {
      const p = msg.payload
      return `time_to_sale=${p.time_to_sale_ms}ms offset=${p.clock_offset_ms}ms`
    }
    case "SCREENSHOT_CAPTURED": {
      const p = msg.payload
      return `#${p.sequence} ${p.state}  ${p.url}`
    }
    case "ERROR": {
      const p = msg.payload
      if (isProtocolError(p)) {
        return `${p.reason}${p.details ? `: ${p.details}` : ""}`
      }
      return `${p.name} ${p.error_type}: ${p.error_message}`
    }
    case "TASK_LOG": {
      const p = msg.payload
      if (isSnapshot(p)) {
        return `snapshot task=${p.task_status} job=${p.job_state}`
      }
      if (isAck(p)) {
        return `${p.action} accepted=${p.accepted} signal=${p.signal_id}`
      }
      if (isHumanGate(p)) {
        return `${p.page_kind} ${p.hint}`
      }
      const detail =
        p.detail === undefined || p.detail === null
          ? ""
          : `  ${typeof p.detail === "string" ? p.detail : JSON.stringify(p.detail)}`
      return `${p.event_type} ${p.name}${detail}`
    }
  }
}

export function toEntry(msg: ServerMessage, seq: number): LogEntry {
  const d = parseServerDate(msg.timestamp)
  return {
    key: entryKey(msg, seq),
    level: deriveLevel(msg),
    time: d ? formatTimeOfDay(d) : "--:--:--.---",
    summary: summarize(msg),
    raw: msg,
  }
}

/**
 * 附加一筆到環形緩衝。
 * - 以 key 去重（重連時伺服器回放最近 50 筆會撞上既有 outbox_id）。
 * - 超過上限丟最舊。
 * 回傳新陣列；未變更時回傳原陣列（讓 React 可以跳過重繪）。
 */
export function appendEntry(
  buffer: LogEntry[],
  entry: LogEntry,
  seen: Set<string>,
  limit = LOG_BUFFER_LIMIT
): LogEntry[] {
  if (seen.has(entry.key)) return buffer
  seen.add(entry.key)
  const next =
    buffer.length >= limit
      ? buffer.slice(buffer.length - limit + 1)
      : buffer.slice()
  next.push(entry)
  return next
}

export function filterEntries(
  entries: LogEntry[],
  levels: Set<LogLevel>,
  search: string
): LogEntry[] {
  const q = search.trim().toLowerCase()
  return entries.filter((e) => {
    if (!levels.has(e.level)) return false
    if (!q) return true
    return e.summary.toLowerCase().includes(q) || e.time.includes(q)
  })
}

/** 複製用純文字：`HH:mm:ss.SSS  LEVEL  <摘要>`。 */
export function entriesToText(entries: LogEntry[]): string {
  return entries
    .map((e) => `${e.time}  ${levelLabel(e.level)}  ${e.summary}`)
    .join("\n")
}
