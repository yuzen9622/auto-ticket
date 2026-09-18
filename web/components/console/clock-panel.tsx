"use client"

import { Countdown } from "@/components/terminal/countdown"
import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { formatDateTime, formatMs } from "@/lib/format"
import type { ClockTickPayload } from "@/lib/ws/types"

/** 與 countdown.tsx 相同的 T-60s 警示窗口。 */
const WARNING_WINDOW_MS = 60_000

/** 時鐘偏移超過此值代表本機時間不可信，標紅提醒。 */
const OFFSET_ALERT_MS = 1000

export function ClockPanel({ clock }: { clock: ClockTickPayload | null }) {
  const timeToSale = clock?.time_to_sale_ms ?? null
  const offset = clock?.clock_offset_ms ?? null

  const withinWarning =
    timeToSale !== null && timeToSale > 0 && timeToSale <= WARNING_WINDOW_MS

  return (
    <Panel title="開賣倒數">
      <div className="flex flex-col gap-2">
        <Countdown timeToSaleMs={timeToSale} />
        <KvRow
          label="time_to_sale"
          value={formatMs(timeToSale)}
          tone={withinWarning ? "text-[var(--oc-warning)]" : undefined}
        />
        <KvRow
          label="clock_offset"
          value={formatMs(offset)}
          tone={
            offset !== null && Math.abs(offset) > OFFSET_ALERT_MS
              ? "text-[var(--oc-danger)]"
              : undefined
          }
        />
        <KvRow label="server_time" value={formatDateTime(clock?.server_time)} />
        {clock === null && (
          <p className="text-[11px] text-[var(--oc-muted)]">
            尚未收到 CLOCK_TICK；Worker 開始執行後每秒更新一次。
          </p>
        )}
      </div>
    </Panel>
  )
}
