"use client"

import { useQuery } from "@tanstack/react-query"

import { ClockPanel } from "@/components/console/clock-panel"
import { ConsoleHeader } from "@/components/console/console-header"
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
import { useTaskSocket } from "@/lib/ws/use-task-socket"

export function LiveConsole({ taskId }: { taskId: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => getTask(taskId),
    // 終態後停止輪詢；其餘 3s 跟上 job_state 變化。
    refetchInterval: (query) =>
      query.state.data && isTaskFinished(query.state.data.task.status)
        ? false
        : 3000,
  })

  const taskStatus = data?.task.status ?? null
  const socket = useTaskSocket(taskId, { taskStatus })

  if (isLoading) return <EmptyState message="載入任務" />
  if (isError || !data) {
    return (
      <EmptyState
        message="無法載入此任務"
        hint={error instanceof Error ? error.message : undefined}
      />
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

      {/* xl 以上鎖死單列高（面板各自內捲）；窄幅改成網格自己捲動，
          兩者都不讓內容擐破 flex-1，底部 ControlBar 才不會被擠出畫面。 */}
      <div className="oc-scroll grid min-h-0 min-w-0 flex-1 grid-cols-1 gap-3 overflow-y-auto xl:grid-cols-[220px_minmax(0,1fr)_260px] xl:grid-rows-[minmax(0,1fr)] xl:overflow-hidden">
        <StateRail
          currentState={currentState}
          visitedStates={socket.visitedStates}
        />

        <div className="flex min-h-0 min-w-0 flex-col gap-3">
          <div className="grid min-w-0 shrink-0 grid-cols-1 gap-3 md:grid-cols-2">
            <ClockPanel clock={socket.clock} />
            <Panel title="任務摘要">
              <KvRow label="目前狀態" value={currentState ?? "—"} />
              <KvRow
                label="已通過狀態"
                value={String(socket.visitedStates.length)}
              />
              <KvRow
                label="scheduled_at"
                value={formatDateTime(data.task.scheduled_at)}
              />
              <KvRow
                label="started_at"
                value={formatDateTime(data.task.started_at)}
              />
              <KvRow
                label="finished_at"
                value={formatDateTime(data.task.finished_at)}
              />
              <KvRow
                label="payment_method"
                value="mock"
                tone="text-[var(--oc-warning)]"
              />
              <p className="pt-1 text-[10px] text-[var(--oc-warning)]">
                MOCK 測試模式 — 不會發生真實付款
              </p>
              {data.task.error_message && (
                <p className="pt-1 text-[11px] text-[var(--oc-danger)]">
                  {data.task.error_message}
                </p>
              )}
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
