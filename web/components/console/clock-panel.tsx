"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

import { Countdown } from "@/components/terminal/countdown"
import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { deriveCountdown } from "@/lib/countdown"
import { formatDateTime, formatMs } from "@/lib/format"
import type { ClockTickPayload } from "@/lib/ws/types"

/** 時鐘偏移超過此值代表本機時間不可信，標紅提醒。 */
const OFFSET_ALERT_MS = 1000

/**
 * 倒數面板。
 *
 * 標題與數字都跟著伺服器給的階段走：開賣前數到開賣，開賣後數到搶票逾時，
 * 任務結束就停下來改說結果，不會一直卡在 00:00:00。
 */
export function ClockPanel({
  clock,
  clockReceivedAt,
  resultLabel,
}: {
  clock: ClockTickPayload | null
  clockReceivedAt: number
  resultLabel: string
}) {
  const t = useTranslations("taskConsole")

  const [now, setNow] = React.useState(() => Date.now())
  const ticking = clock !== null && clock.phase !== "finished"
  React.useEffect(() => {
    if (!ticking) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [ticking])

  const view = deriveCountdown(clock, now, clockReceivedAt)
  const offset = clock?.clock_offset_ms ?? null

  const title =
    view.phase === "ticketing"
      ? t("ticketingCountdown")
      : view.phase === "finished"
        ? t("countdownFinished")
        : t("saleCountdown")

  return (
    <Panel title={title}>
      <div className="flex flex-col gap-2">
        {view.phase === "finished" ? (
          <p aria-live="polite" className="text-sm font-semibold">
            {resultLabel}
          </p>
        ) : (
          <Countdown remainingMs={view.remainingMs} />
        )}

        <KvRow
          label={t("timeToSale")}
          value={formatMs(clock?.time_to_sale_ms ?? null)}
        />
        <KvRow
          label={t("timeToTimeout")}
          value={formatMs(clock?.time_to_timeout_ms ?? null)}
        />
        <KvRow
          label={t("clockOffset")}
          value={formatMs(offset)}
          tone={
            offset !== null && Math.abs(offset) > OFFSET_ALERT_MS
              ? "text-destructive"
              : undefined
          }
        />
        <KvRow
          label={t("serverTime")}
          value={formatDateTime(clock?.server_time)}
        />

        {clock === null && (
          <p className="text-xs text-muted-foreground">
            {t("waitingForClock")}
          </p>
        )}
      </div>
    </Panel>
  )
}
