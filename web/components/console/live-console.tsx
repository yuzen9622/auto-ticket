"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { useQuery } from "@tanstack/react-query"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/animate-ui/components/radix/accordion"
import { ClockPanel } from "@/components/console/clock-panel"
import { ConsoleHeader } from "@/components/console/console-header"
import { HumanGateBanner } from "@/components/console/human-gate-banner"
import { ControlBar } from "@/components/console/control-bar"
import { LogStream } from "@/components/console/log-stream"
import { ScreenshotStrip } from "@/components/console/screenshot-strip"
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
  useJobStateLabel,
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
  const jobStateLabel = useJobStateLabel()
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
  const jobState = socket.snapshotStatus?.job_state ?? data.job_state

  // h-full 讓中欄的 flex-1 有可填的高度，否則日誌區會被壓成 0 高。
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-3">
      <ConsoleHeader
        detail={{ ...data, job_state: jobState }}
        wsStatus={socket.status}
      />

      <HumanGateBanner gate={socket.humanGate} />

      {/* xl 以上鎖死單列高（面板各自內捲）；窄幅改成網格自己捲動，
          兩者都不讓內容撐破 flex-1，底部 ControlBar 才不會被擠出畫面。 */}
      <div className="grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-3 overflow-y-auto xl:grid-cols-[220px_minmax(0,1fr)_260px] xl:grid-rows-[minmax(0,1fr)] xl:overflow-hidden">
        <StateRail
          currentState={currentState}
          visitedStates={socket.visitedStates}
        />

        <div className="flex min-h-0 min-w-0 flex-col gap-3">
          <div className="grid min-w-0 shrink-0 grid-cols-1 gap-3 md:grid-cols-2">
            <ClockPanel
              clock={socket.clock}
              clockReceivedAt={socket.clockReceivedAt}
              resultLabel={resultLabel(taskStatus, currentState)}
            />

            <Panel title={t("summaryHeading")}>
              <KvRow
                label={t("currentState")}
                value={
                  currentState
                    ? purchaseStateLabel(currentState)
                    : common("none")
                }
              />
              <KvRow
                label={t("visitedStates")}
                value={String(socket.visitedStates.length)}
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

              {/* 技術資訊預設收起：一般使用者不需要看到內部識別碼。 */}
              <Accordion type="single" collapsible className="pt-2">
                <AccordionItem value="technical-details" className="border-0">
                  <AccordionTrigger className="py-0 text-xs font-normal text-muted-foreground hover:no-underline">
                    {t("technicalDetails")}
                  </AccordionTrigger>
                  <AccordionContent className="pt-1 pb-0">
                    <KvRow label={t("taskId")} value={data.task.id} />
                    <KvRow
                      label={t("jobId")}
                      value={data.job_id ?? common("none")}
                    />
                    <KvRow
                      label={t("jobState")}
                      value={
                        jobState ? jobStateLabel(jobState) : common("none")
                      }
                    />
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </Panel>
          </div>

          <LogStream entries={socket.entries} />
        </div>

        <ScreenshotStrip shots={socket.screenshots} />
      </div>

      <div className="shrink-0">
        <ControlBar connected={socket.status === "open"} onSend={socket.send} />
      </div>
    </div>
  )
}
