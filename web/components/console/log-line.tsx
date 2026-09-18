"use client"

import { Copy } from "lucide-react"
import { useTranslations } from "next-intl"

import { levelLabel, type LogEntry, type LogLevel } from "@/lib/log-buffer"
import { cn } from "@/lib/utils"

export const LEVEL_TEXT_CLASS: Record<LogLevel, string> = {
  error: "text-destructive",
  state: "text-primary",
  shot: "text-muted-foreground",
  tick: "text-amber-600 dark:text-amber-400",
  info: "text-foreground",
  snap: "text-emerald-600 dark:text-emerald-400",
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
  const t = useTranslations("taskConsole")
  const text = `${entry.time}  ${levelLabel(entry.level)}  ${entry.summary}`

  return (
    <div
      style={{ height }}
      className="group flex min-w-0 items-center gap-2 overflow-hidden px-3 font-mono text-xs leading-none hover:bg-muted/50"
    >
      <span className="tabular shrink-0 text-muted-foreground">
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
        aria-label={t("copyLine")}
        title={t("copyLine")}
        onClick={() => onCopy(text)}
        className="shrink-0 cursor-pointer text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
      >
        <Copy className="size-3" aria-hidden />
      </button>
    </div>
  )
}
