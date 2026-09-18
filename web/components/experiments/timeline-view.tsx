"use client"

import * as React from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import { EmptyState } from "@/components/terminal/empty-state"
import { Panel } from "@/components/terminal/panel"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import type { ExperimentEventOut } from "@/lib/api/types"
import { screenshotUrl } from "@/lib/config"
import { formatMs } from "@/lib/format"
import { purchaseStateTone, TONE_TEXT_CLASS } from "@/lib/fsm"

function TimelineRow({ event }: { event: ExperimentEventOut }) {
  const [open, setOpen] = React.useState(false)
  const hasDetails =
    event.details !== null && Object.keys(event.details).length > 0

  return (
    <li className="border-b border-[var(--oc-border)] last:border-b-0">
      <div className="flex items-start gap-3 px-3 py-2 text-[12px] hover:bg-[var(--oc-surface-2)]">
        <span className="tabular w-16 shrink-0 text-right text-[var(--oc-muted)]">
          {formatMs(event.elapsed_ms)}
        </span>
        <span className="tabular w-8 shrink-0 text-[var(--oc-muted)]">
          #{event.sequence}
        </span>
        <span className="w-28 shrink-0 truncate">{event.stage}</span>
        <span className="w-56 shrink-0 truncate">
          {event.state_from || event.state_to ? (
            <>
              <span
                className={
                  TONE_TEXT_CLASS[purchaseStateTone(event.state_from ?? "")]
                }
              >
                {event.state_from ?? "—"}
              </span>
              <span className="text-[var(--oc-muted)]"> → </span>
              <span
                className={
                  TONE_TEXT_CLASS[purchaseStateTone(event.state_to ?? "")]
                }
              >
                {event.state_to ?? "—"}
              </span>
            </>
          ) : (
            <span className="text-[var(--oc-muted)]">—</span>
          )}
        </span>
        <span className="min-w-0 flex-1 truncate">{event.action}</span>

        {event.screenshot_url && (
          <Dialog>
            <DialogTrigger asChild>
              <button
                type="button"
                className="shrink-0 rounded-[4px] border border-[var(--oc-border)] p-0"
              >
                {/* 原生 img：截圖僅供除錯，不需 Image Optimizer。 */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={screenshotUrl(event.screenshot_url)}
                  alt={`步驟 ${event.sequence} 截圖`}
                  className="h-10 w-16 object-cover"
                />
              </button>
            </DialogTrigger>
            <DialogContent className="max-w-5xl">
              <DialogHeader>
                <DialogTitle>
                  #{event.sequence} {event.stage}
                </DialogTitle>
              </DialogHeader>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={screenshotUrl(event.screenshot_url)}
                alt={`步驟 ${event.sequence} 截圖`}
                className="max-h-[75vh] w-full object-contain"
              />
            </DialogContent>
          </Dialog>
        )}

        {hasDetails && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="shrink-0 text-[var(--oc-muted)] hover:text-[var(--oc-fg)]"
          >
            {open ? (
              <ChevronDown className="size-3.5" />
            ) : (
              <ChevronRight className="size-3.5" />
            )}
          </button>
        )}
      </div>

      {open && hasDetails && (
        <pre className="oc-scroll oc-enter overflow-x-auto bg-[var(--oc-sunken)] px-3 py-2 text-[11px] text-[var(--oc-muted)]">
          {JSON.stringify(event.details, null, 2)}
        </pre>
      )}
    </li>
  )
}

export function TimelineView({ events }: { events: ExperimentEventOut[] }) {
  const sorted = React.useMemo(
    () => [...events].sort((a, b) => a.sequence - b.sequence),
    [events]
  )

  return (
    <Panel title="時間軸" bodyClassName="p-0">
      {sorted.length === 0 ? (
        <EmptyState message="此實驗沒有事件紀錄" />
      ) : (
        <ul className="oc-scroll max-h-[70vh] overflow-y-auto">
          {sorted.map((e) => (
            <TimelineRow key={e.id} event={e} />
          ))}
        </ul>
      )}
    </Panel>
  )
}
