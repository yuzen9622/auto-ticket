"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { useQuery } from "@tanstack/react-query"

import { ClockPanel } from "@/components/console/clock-panel"
import { ConsoleHeader } from "@/components/console/console-header"
import { AutomationStatusBanner } from "@/components/console/automation-status-banner"
import { HumanGateBanner } from "@/components/console/human-gate-banner"
import { ControlBar } from "@/components/console/control-bar"
import { StateRail } from "@/components/console/state-rail"
import { EmptyState } from "@/components/terminal/empty-state"
import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { getTask } from "@/lib/api/tasks"
import { formatDateTime } from "@/lib/format"
import { isTaskFinished } from "@/lib/fsm"
import { useApiErrorMessage } from "@/lib/i18n/errors"
import {
  useExecutionModeLabel,
  usePurchaseStateLabel,
} from "@/lib/i18n/labels"
import { useTaskSocket } from "@/lib/ws/use-task-socket"

/** 倒數停止後要說的結果，取自任務終態與最後一個購票狀態。 */
function useResultLabel() {
  const t = useTranslations("taskConsole")
  return React.useCallback(
    (taskStatus: string | null, purchaseState: string | null): string => {
      if (purchaseState === "SOLD_OUT") return t("resultSoldOut")
      if (purchaseState === "TIMEOUT") return t("resultTimeout")
      switch (taskStatus) {
        case "COMPLETED":
          return t("resultCompleted")
        case "FAILED":
          return t("resultFailed")
        case "CANCELLED":
          return t("resultCancelled")
        default:
          return t("resultFinished")
      }
    },
    [t]
  )
}

export function LiveConsole({ taskId }: { taskId: string }) {
  const t = useTranslations("taskConsole")
  const common = useTranslations("common")
  const apiErrorMessage = useApiErrorMessage()
  const purchaseStateLabel = usePurchaseStateLabel()
  const executionModeLabel = useExecutionModeLabel()
  const resultLabel = useResultLabel()

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => getTask(taskId),
    // 終態後停止輪詢；其餘 3s 跟上執行狀態變化。
    refetchInterval: (query) =>
      query.state.data && isTaskFinished(query.state.data.task.status)
        ? false
        : 3000,
  })

  const taskStatus = data?.task.status ?? null
  const socket = useTaskSocket(taskId, { taskStatus })

  if (isLoading) return <EmptyState message={t("loading")} />
  if (isError || !data) {
    return (
      <EmptyState message={t("loadFailed")} hint={apiErrorMessage(error)} />
    )
  }

  const currentState = socket.currentState

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <ConsoleHeader detail={data} wsStatus={socket.status} />

      <AutomationStatusBanner
        status={socket.humanGate ? null : socket.automation}
      />
      <HumanGateBanner gate={socket.humanGate} />

      <div className="grid min-w-0 grid-cols-1 gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
        <StateRail
          currentState={currentState}
          visitedStates={socket.visitedStates}
        />

        <div className="grid min-w-0 grid-cols-1 gap-3 md:grid-cols-2">
          <ClockPanel
            clock={socket.clock}
            clockReceivedAt={socket.clockReceivedAt}
            resultLabel={resultLabel(taskStatus, currentState)}
          />

          <Panel title={t("summaryHeading")}>
            <KvRow
              label={t("currentState")}
              value={
                currentState ? purchaseStateLabel(currentState) : common("none")
              }
            />
            <KvRow
              label={t("executionMode")}
              value={executionModeLabel(data.task.execution_mode)}
            />
            <KvRow
              label={t("scheduledAt")}
              value={formatDateTime(data.task.scheduled_at)}
            />
            <KvRow
              label={t("startedAt")}
              value={formatDateTime(data.task.started_at)}
            />
            <KvRow
              label={t("finishedAt")}
              value={formatDateTime(data.task.finished_at)}
            />

            {data.task.error_message && (
              <p className="pt-1 text-xs text-destructive">
                {data.task.error_message}
              </p>
            )}
          </Panel>
        </div>
      </div>

      <ControlBar connected={socket.status === "open"} onSend={socket.send} />
    </div>
  )
}
