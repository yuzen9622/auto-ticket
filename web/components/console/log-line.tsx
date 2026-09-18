"use client"

import { Copy } from "lucide-react"

import { levelLabel, type LogEntry, type LogLevel } from "@/lib/log-buffer"
import { cn } from "@/lib/utils"

export const LEVEL_TEXT_CLASS: Record<LogLevel, string> = {
  error: "text-[var(--oc-danger)]",
  state: "text-[var(--oc-accent)]",
  shot: "text-[var(--oc-muted)]",
  tick: "text-[var(--oc-warning)]",
  info: "text-[var(--oc-fg)]",
  snap: "text-[var(--oc-success)]",
}

/** 單行日誌：`HH:mm:ss.SSS LEVEL <摘要>`，LEVEL 欄固定 5 字元寬。 */
export function LogLine({
  entry,
  height,
  onCopy,
}: {
  entry: LogEntry
  height: number
  onCopy: (text: string) => void
}) {
  const text = `${entry.time}  ${levelLabel(entry.level)}  ${entry.summary}`

  return (
    <div
      style={{ height }}
      className="group flex min-w-0 items-center gap-2 overflow-hidden px-3 leading-none hover:bg-[var(--oc-surface-2)]"
    >
      <span className="tabular shrink-0 text-[var(--oc-muted)]">
        {entry.time}
      </span>
      <span
        className={cn(
          "shrink-0 tracking-wider whitespace-pre",
          LEVEL_TEXT_CLASS[entry.level]
        )}
      >
        {levelLabel(entry.level)}
      </span>
      <span className="min-w-0 flex-1 truncate" title={entry.summary}>
        {entry.summary}
      </span>
      <button
        type="button"
        aria-label="複製此行"
        title="複製此行"
        onClick={() => onCopy(text)}
        className="shrink-0 text-[var(--oc-muted)] opacity-0 group-hover:opacity-100 hover:text-[var(--oc-fg)] focus-visible:opacity-100"
      >
        <Copy className="size-3" aria-hidden />
      </button>
    </div>
  )
}
