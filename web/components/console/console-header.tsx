"use client"

import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { CopyButton } from "@/components/terminal/copy-button"
import { StateBadge, TaskStatusBadge } from "@/components/terminal/state-badge"
import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/api/client"
import {
  cancelTask,
  isCancellable,
  isStartable,
  startTask,
} from "@/lib/api/tasks"
import type { TaskDetailResponse } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"
import { jobStateTone, TONE_DOT_CLASS, TONE_TEXT_CLASS } from "@/lib/fsm"
import {
  PAGE_STATE_LABEL,
  PAGE_STATE_TONE,
  WS_STATUS_LABEL,
  WS_STATUS_TONE,
  derivePageState,
  type WsStatus,
} from "@/lib/task-status"
import { cn } from "@/lib/utils"

function eventTitle(spec: Record<string, unknown>): string {
  const title = spec.event_title
  return typeof title === "string" && title !== "" ? title : "（無標題）"
}

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "操作失敗"
}

export function ConsoleHeader({
  detail,
  wsStatus,
}: {
  detail: TaskDetailResponse
  wsStatus: WsStatus
}) {
  const qc = useQueryClient()
  const task = detail.task
  const pageState = derivePageState(task.status, wsStatus)

  const invalidate = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["task", task.id] })
    void qc.invalidateQueries({ queryKey: ["tasks"] })
  }, [qc, task.id])

  const start = useMutation({
    mutationFn: () => startTask(task.id),
    onSuccess: (r) => {
      toast.success(r.triggered ? "已觸發執行" : "已接受，等待排程")
      invalidate()
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  const cancel = useMutation({
    mutationFn: () => cancelTask(task.id),
    onSuccess: (r) => {
      toast.success(`已取消（${r.status}）`)
      invalidate()
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  return (
    <header className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-[4px] border border-[var(--oc-border)] bg-[var(--oc-surface)] px-3 py-2">
      {/* basis-64 搭配 header 的 flex-wrap：空間不足時整欄換行，而不是被壓到比複製鈕還窄。 */}
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-0.5">
        <h1 className="truncate text-[15px] font-bold">
          {eventTitle(task.spec)}
        </h1>
        <div className="flex min-w-0 flex-wrap items-center gap-x-1 text-[11px] text-[var(--oc-muted)]">
          <span className="tabular min-w-0 truncate">{task.id}</span>
          <CopyButton value={task.id} label="複製 task_id" />
          <span className="shrink-0">·</span>
          <span className="min-w-0 truncate">
            建立於 {formatDateTime(task.created_at)}
          </span>
        </div>
      </div>

      <dl className="flex shrink-0 flex-col gap-0.5 text-[11px]">
        <div className="flex items-baseline gap-2">
          <dt className="text-[var(--oc-muted)]">job_id</dt>
          <dd className="tabular">{detail.job_id ?? "—"}</dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="text-[var(--oc-muted)]">job_state</dt>
          <dd
            className={cn(
              "tabular",
              detail.job_state
                ? TONE_TEXT_CLASS[jobStateTone(detail.job_state)]
                : "text-[var(--oc-muted)]"
            )}
          >
            {detail.job_state ?? "—"}
          </dd>
        </div>
      </dl>

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <TaskStatusBadge status={task.status} announce />
        <StateBadge
          value={pageState}
          tone={PAGE_STATE_TONE[pageState]}
          label={PAGE_STATE_LABEL[pageState]}
        />
        <span
          title={`WebSocket ${wsStatus}`}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-[4px] border border-[var(--oc-border)] px-2 py-0.5 text-[11px] tracking-wider uppercase",
            TONE_TEXT_CLASS[WS_STATUS_TONE[wsStatus]]
          )}
        >
          <span
            aria-hidden
            className={cn(
              "size-2 shrink-0 rounded-full",
              TONE_DOT_CLASS[WS_STATUS_TONE[wsStatus]]
            )}
          />
          WS {WS_STATUS_LABEL[wsStatus]}
        </span>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="accent"
          size="sm"
          disabled={!isStartable(task.status) || start.isPending}
          onClick={() => start.mutate()}
        >
          啟動
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!isCancellable(task.status) || cancel.isPending}
          onClick={() => cancel.mutate()}
        >
          取消
        </Button>
      </div>
    </header>
  )
}
