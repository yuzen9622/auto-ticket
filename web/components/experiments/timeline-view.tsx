"use client"

import * as React from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { useTranslations } from "next-intl"

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
import { usePurchaseStateLabel } from "@/lib/i18n/labels"

function TimelineRow({ event }: { event: ExperimentEventOut }) {
  const [open, setOpen] = React.useState(false)
  const t = useTranslations("history")
  const tc = useTranslations("common")
  const purchaseStateLabel = usePurchaseStateLabel()
  const hasDetails =
    event.details !== null && Object.keys(event.details).length > 0

  return (
    <li className="border-b border-border last:border-b-0">
      <div className="flex items-start gap-3 px-3 py-2 text-xs hover:bg-muted/50">
        <span className="tabular w-16 shrink-0 text-right text-muted-foreground">
          {formatMs(event.elapsed_ms)}
        </span>
        <span className="tabular w-8 shrink-0 text-muted-foreground">
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
                {event.state_from
                  ? purchaseStateLabel(event.state_from)
                  : tc("none")}
              </span>
              <span className="text-muted-foreground"> → </span>
              <span
                className={
                  TONE_TEXT_CLASS[purchaseStateTone(event.state_to ?? "")]
                }
              >
                {event.state_to
                  ? purchaseStateLabel(event.state_to)
                  : tc("none")}
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">{tc("none")}</span>
          )}
        </span>
        <span className="min-w-0 flex-1 truncate">{event.action}</span>

        {event.screenshot_url && (
          <Dialog>
            <DialogTrigger asChild>
              <button
                type="button"
                className="shrink-0 cursor-pointer overflow-hidden rounded-md border border-border p-0"
              >
                {/* 原生 img：截圖僅供除錯，不需 Image Optimizer。 */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={screenshotUrl(event.screenshot_url)}
                  alt={t("screenshotStep", { sequence: event.sequence })}
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
                alt={t("screenshotStep", { sequence: event.sequence })}
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
            className="shrink-0 cursor-pointer text-muted-foreground hover:text-foreground"
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
        <pre className="overflow-x-auto bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          {JSON.stringify(event.details, null, 2)}
        </pre>
      )}
    </li>
  )
}

export function TimelineView({ events }: { events: ExperimentEventOut[] }) {
  const t = useTranslations("history")
  const sorted = React.useMemo(
    () => [...events].sort((a, b) => a.sequence - b.sequence),
    [events]
  )

  return (
    <Panel title={t("timeline")} bodyClassName="p-0">
      {sorted.length === 0 ? (
        <EmptyState message={t("noEvents")} />
      ) : (
        <ul className="max-h-[70vh] overflow-y-auto">
          {sorted.map((e) => (
            <TimelineRow key={e.id} event={e} />
          ))}
        </ul>
      )}
    </Panel>
  )
}
