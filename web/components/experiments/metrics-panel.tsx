import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import type { ExperimentMetricsOut, ExperimentOut } from "@/lib/api/types"
import { formatDateTime, formatMs } from "@/lib/format"

/** ExperimentMetricsOut 全 10 欄；null 顯示 `—`，刻意不顯示 0。 */
const METRIC_ROWS: {
  key: keyof ExperimentMetricsOut
  label: string
  kind: "ms" | "count"
}[] = [
  { key: "scheduler_error_ms", label: "scheduler_error", kind: "ms" },
  { key: "sale_detection_ms", label: "sale_detection", kind: "ms" },
  { key: "event_page_load_ms", label: "event_page_load", kind: "ms" },
  { key: "ticket_selection_ms", label: "ticket_selection", kind: "ms" },
  { key: "seat_selection_ms", label: "seat_selection", kind: "ms" },
  { key: "form_fill_ms", label: "form_fill", kind: "ms" },
  { key: "verification_ms", label: "verification", kind: "ms" },
  { key: "payment_ms", label: "payment", kind: "ms" },
  { key: "retry_count", label: "retry_count", kind: "count" },
  { key: "selector_fallback_count", label: "selector_fallback", kind: "count" },
]

export function MetricsPanel({
  experiment,
  metrics,
}: {
  experiment: ExperimentOut
  metrics: ExperimentMetricsOut | null
}) {
  return (
    <div className="flex flex-col gap-4">
      <Panel title="總覽">
        <KvRow label="experiment_id" value={experiment.id} />
        <KvRow label="task_id" value={experiment.task_id ?? "—"} />
        <KvRow label="final_state" value={experiment.final_state} />
        <KvRow
          label="success"
          value={experiment.success ? "成功" : "失敗"}
          tone={
            experiment.success
              ? "text-[var(--oc-success)]"
              : "text-[var(--oc-danger)]"
          }
        />
        <KvRow label="strategy_used" value={experiment.strategy_used} />
        <KvRow label="clock_sync_mode" value={experiment.clock_sync_mode} />
        <KvRow
          label="sale_time_error"
          value={formatMs(experiment.sale_time_error_ms)}
        />
        <KvRow
          label="total_duration"
          value={formatMs(experiment.total_duration_ms)}
        />
        <KvRow
          label="created_at"
          value={formatDateTime(experiment.created_at)}
        />
      </Panel>

      <Panel title="指標">
        {metrics ? (
          METRIC_ROWS.map((row) => {
            const raw = metrics[row.key]
            const value =
              row.kind === "ms"
                ? formatMs(typeof raw === "number" ? raw : null)
                : typeof raw === "number"
                  ? String(raw)
                  : "—"
            return <KvRow key={row.key} label={row.label} value={value} />
          })
        ) : (
          <p className="text-[12px] text-[var(--oc-muted)]">
            此實驗沒有指標資料。
          </p>
        )}
      </Panel>
    </div>
  )
}
