"use client"

import { useTranslations } from "next-intl"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { usePurchaseStateLabel } from "@/lib/i18n/labels"
import type { ExperimentMetricsOut, ExperimentOut } from "@/lib/api/types"
import { formatDateTime, formatMs } from "@/lib/format"

/** ExperimentMetricsOut 全 10 欄；null 顯示 `—`，刻意不顯示 0。 */
const METRIC_ROWS: {
  key: keyof ExperimentMetricsOut
  labelKey:
    | "metricSchedulerError"
    | "metricSaleDetection"
    | "metricEventPageLoad"
    | "metricTicketSelection"
    | "metricSeatSelection"
    | "metricFormFill"
    | "metricVerification"
    | "metricPayment"
    | "metricRetryCount"
    | "metricSelectorFallback"
  kind: "ms" | "count"
}[] = [
  { key: "scheduler_error_ms", labelKey: "metricSchedulerError", kind: "ms" },
  { key: "sale_detection_ms", labelKey: "metricSaleDetection", kind: "ms" },
  { key: "event_page_load_ms", labelKey: "metricEventPageLoad", kind: "ms" },
  { key: "ticket_selection_ms", labelKey: "metricTicketSelection", kind: "ms" },
  { key: "seat_selection_ms", labelKey: "metricSeatSelection", kind: "ms" },
  { key: "form_fill_ms", labelKey: "metricFormFill", kind: "ms" },
  { key: "verification_ms", labelKey: "metricVerification", kind: "ms" },
  { key: "payment_ms", labelKey: "metricPayment", kind: "ms" },
  { key: "retry_count", labelKey: "metricRetryCount", kind: "count" },
  {
    key: "selector_fallback_count",
    labelKey: "metricSelectorFallback",
    kind: "count",
  },
]

export function MetricsPanel({
  experiment,
  metrics,
}: {
  experiment: ExperimentOut
  metrics: ExperimentMetricsOut | null
}) {
  const t = useTranslations("history")
  const purchaseStateLabel = usePurchaseStateLabel()

  return (
    <div className="flex flex-col gap-4">
      <Panel title={t("overview")}>
        <KvRow label={t("recordId")} value={experiment.id} />
        <KvRow label={t("taskId")} value={experiment.task_id ?? "—"} />
        <KvRow
          label={t("finalState")}
          value={purchaseStateLabel(experiment.final_state)}
        />
        <KvRow
          label={t("columnResult")}
          value={experiment.success ? t("success") : t("failure")}
          tone={
            experiment.success
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive"
          }
        />
        <KvRow
          label={t("saleTimeError")}
          value={formatMs(experiment.sale_time_error_ms)}
        />
        <KvRow
          label={t("totalDuration")}
          value={formatMs(experiment.total_duration_ms)}
        />
        <KvRow
          label={t("createdAt")}
          value={formatDateTime(experiment.created_at)}
        />
      </Panel>

      <Panel title={t("metrics")}>
        {metrics ? (
          METRIC_ROWS.map((row) => {
            const raw = metrics[row.key]
            const value =
              row.kind === "ms"
                ? formatMs(typeof raw === "number" ? raw : null)
                : typeof raw === "number"
                  ? String(raw)
                  : "—"
            return <KvRow key={row.key} label={t(row.labelKey)} value={value} />
          })
        ) : (
          <p className="text-xs text-muted-foreground">{t("noMetrics")}</p>
        )}
      </Panel>
    </div>
  )
}
