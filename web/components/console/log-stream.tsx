"use client"

import * as React from "react"
import { ArrowDown } from "lucide-react"
import { toast } from "sonner"

import { LEVEL_TEXT_CLASS, LogLine } from "@/components/console/log-line"
import { Panel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  entriesToText,
  filterEntries,
  type LogEntry,
  type LogLevel,
} from "@/lib/log-buffer"
import { cn } from "@/lib/utils"

/** 行高固定，windowing 才能用純算術推出可視範圍。 */
const ROW_HEIGHT = 20

/** 超過此筆數才啟用 windowing（計畫 §3.7）。 */
const VIRTUALIZE_THRESHOLD = 500

/** 可視範圍外多渲染幾行，快速捲動時不留白。 */
const OVERSCAN = 12

/** 離底部超過此距離即視為使用者主動上捲，自動暫停跟隨。 */
const AUTO_SCROLL_EPSILON_PX = 48

const LEVELS: { level: LogLevel; label: string }[] = [
  { level: "info", label: "INFO" },
  { level: "state", label: "STATE" },
  { level: "error", label: "ERROR" },
  { level: "snap", label: "SNAP" },
  { level: "shot", label: "SHOT" },
  { level: "tick", label: "TICK" },
]

/** CLOCK_TICK 預設不顯示：1 Hz 會淹沒畫面（計畫 §3.4 第 4 點）。 */
const DEFAULT_LEVELS: LogLevel[] = ["info", "state", "error", "snap", "shot"]

export function LogStream({ entries }: { entries: LogEntry[] }) {
  const [levels, setLevels] = React.useState<Set<LogLevel>>(
    () => new Set(DEFAULT_LEVELS)
  )
  const [search, setSearch] = React.useState("")
  const [following, setFollowing] = React.useState(true)
  const [pendingCount, setPendingCount] = React.useState(0)
  const [scrollTop, setScrollTop] = React.useState(0)
  const [viewportHeight, setViewportHeight] = React.useState(0)

  const scrollRef = React.useRef<HTMLDivElement>(null)

  const visible = React.useMemo(
    () => filterEntries(entries, levels, search),
    [entries, levels, search]
  )

  const scrollToBottom = React.useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    setFollowing(true)
    setPendingCount(0)
  }, [])

  // 跟隨模式下每次新訊息都貼底；暫停時只累計未讀數。
  const prevCountRef = React.useRef(visible.length)
  React.useEffect(() => {
    const delta = visible.length - prevCountRef.current
    prevCountRef.current = visible.length
    if (following) {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    } else if (delta > 0) {
      setPendingCount((n) => n + delta)
    }
  }, [visible.length, following])

  React.useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    setViewportHeight(el.clientHeight)
    const observer = new ResizeObserver(() =>
      setViewportHeight(el.clientHeight)
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const onScroll = React.useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    setScrollTop(el.scrollTop)
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distanceFromBottom > AUTO_SCROLL_EPSILON_PX) {
      setFollowing(false)
    } else {
      setFollowing(true)
      setPendingCount(0)
    }
  }, [])

  const copyText = React.useCallback((text: string) => {
    void navigator.clipboard.writeText(text).then(
      () => toast.success("已複製"),
      () => toast.error("複製失敗，瀏覽器未授權剪貼簿")
    )
  }, [])

  const toggleLevel = (level: LogLevel) => {
    setLevels((prev) => {
      const next = new Set(prev)
      if (next.has(level)) next.delete(level)
      else next.add(level)
      return next
    })
  }

  const virtualized = visible.length > VIRTUALIZE_THRESHOLD
  const rowCount = virtualized
    ? Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN * 2
    : visible.length
  const startIndex = virtualized
    ? Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
    : 0
  const windowRows = virtualized
    ? visible.slice(startIndex, startIndex + rowCount)
    : visible
  const padTop = startIndex * ROW_HEIGHT
  const padBottom = virtualized
    ? Math.max(
        0,
        (visible.length - startIndex - windowRows.length) * ROW_HEIGHT
      )
    : 0

  return (
    <Panel
      title={`即時日誌 · ${visible.length} / ${entries.length}`}
      bodyClassName="flex min-h-0 min-w-0 flex-col overflow-hidden p-0"
      className="min-h-[180px] min-w-0 flex-1 overflow-hidden"
      actions={
        <Button
          variant="ghost"
          size="sm"
          onClick={() => copyText(entriesToText(visible))}
          disabled={visible.length === 0}
        >
          複製全部
        </Button>
      }
    >
      <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-[var(--oc-border)] px-3 py-1.5">
        <div className="flex min-w-0 flex-wrap items-center gap-1">
          {LEVELS.map(({ level, label }) => {
            const on = levels.has(level)
            return (
              <button
                key={level}
                type="button"
                aria-pressed={on}
                onClick={() => toggleLevel(level)}
                className={cn(
                  "shrink-0 rounded-[4px] border px-2 py-0.5 text-[10px] tracking-wider transition-colors duration-150 ease-out",
                  on
                    ? cn(
                        "border-[var(--oc-border)] bg-[var(--oc-surface-2)]",
                        LEVEL_TEXT_CLASS[level]
                      )
                    : "border-transparent text-[var(--oc-muted)] hover:bg-[var(--oc-surface-2)]"
                )}
              >
                {label}
              </button>
            )
          })}
        </div>
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜尋日誌"
          aria-label="搜尋日誌"
          className="h-6 w-full min-w-0 text-[11px] sm:ml-auto sm:w-48"
          autoComplete="off"
        />
      </div>

      <div className="relative min-h-0 min-w-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="oc-scroll absolute inset-0 overflow-x-hidden overflow-y-auto bg-[var(--oc-sunken)] py-1 text-[12px]"
        >
          {visible.length === 0 ? (
            <p className="px-3 py-6 text-center text-[11px] text-[var(--oc-muted)]">
              {entries.length === 0
                ? "尚無日誌；連線後會先收到 snapshot。"
                : "目前的等級篩選或搜尋條件沒有符合的日誌。"}
            </p>
          ) : (
            <>
              {padTop > 0 && <div style={{ height: padTop }} aria-hidden />}
              {windowRows.map((entry) => (
                <LogLine
                  key={entry.key}
                  entry={entry}
                  height={ROW_HEIGHT}
                  onCopy={copyText}
                />
              ))}
              {padBottom > 0 && (
                <div style={{ height: padBottom }} aria-hidden />
              )}
            </>
          )}
        </div>

        {!following && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="absolute bottom-2 left-1/2 flex max-w-[calc(100%-1rem)] -translate-x-1/2 items-center gap-1.5 rounded-[4px] border border-[var(--oc-accent)] bg-[var(--oc-surface)] px-3 py-1 text-[11px] whitespace-nowrap text-[var(--oc-accent)]"
          >
            <ArrowDown className="size-3" aria-hidden />
            已暫停 · {pendingCount} 筆新訊息
          </button>
        )}
      </div>
    </Panel>
  )
}
