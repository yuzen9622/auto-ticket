"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { CopyButton } from "@/components/terminal/copy-button"
import { StateBadge, TaskStatusBadge } from "@/components/terminal/state-badge"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ApiError } from "@/lib/api/client"
import {
  cancelTask,
  isCancellable,
  isStartable,
  startTask,
} from "@/lib/api/tasks"
import type { TaskDetailResponse } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"
import { TONE_DOT_CLASS, TONE_TEXT_CLASS } from "@/lib/fsm"
import { useApiErrorMessage } from "@/lib/i18n/errors"
import {
  usePageStateLabel,
  useTaskStatusLabel,
  useWsStatusLabel,
} from "@/lib/i18n/labels"
import {
  PAGE_STATE_TONE,
  WS_STATUS_TONE,
  derivePageState,
  type WsStatus,
} from "@/lib/task-status"
import { cn } from "@/lib/utils"

function eventTitle(spec: Record<string, unknown>, fallback: string): string {
  const title = spec.event_title
  return typeof title === "string" && title !== "" ? title : fallback
}

export function ConsoleHeader({
  detail,
  wsStatus,
}: {
  detail: TaskDetailResponse
  wsStatus: WsStatus
}) {
  const t = useTranslations("taskConsole")
  const tList = useTranslations("taskList")
  const common = useTranslations("common")
  const apiErrorMessage = useApiErrorMessage()
  const pageStateLabel = usePageStateLabel()
  const wsStatusLabel = useWsStatusLabel()
  const taskStatusLabel = useTaskStatusLabel()

  const qc = useQueryClient()
  const task = detail.task
  const pageState = derivePageState(task.status, wsStatus)

  const invalidate = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["task", task.id] })
    void qc.invalidateQueries({ queryKey: ["tasks"] })
  }, [qc, task.id])

  const onError = (error: unknown) =>
    toast.error(
      error instanceof ApiError ? apiErrorMessage(error) : tList("actionFailed")
    )

  const start = useMutation({
    mutationFn: () => startTask(task.id),
    onSuccess: (r) => {
      toast.success(r.triggered ? tList("started") : tList("queued"))
      invalidate()
    },
    onError,
  })

  const cancel = useMutation({
    mutationFn: () => cancelTask(task.id),
    onSuccess: (r) => {
      // 後端回的是原始狀態，播報前先翻譯。
      toast.success(tList("cancelled", { status: taskStatusLabel(r.status) }))
      invalidate()
    },
    onError,
  })

  return (
    <header className="flex min-w-0 shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-3 pb-1">
      {/* basis-64 搭配 header 的 flex-wrap：空間不足時整欄換行，而不是被壓到比複製鈕還窄。 */}
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-0.5">
        <h1 className="truncate text-lg font-semibold tracking-tight">
          {eventTitle(task.spec, t("untitled"))}
        </h1>
        <div className="flex min-w-0 flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
          <span className="tabular min-w-0 truncate">{task.id}</span>
          <CopyButton
            value={task.id}
            label={`${common("copy")}${t("taskId")}`}
          />
          <span className="shrink-0">·</span>
          <span className="min-w-0 truncate">
            {t("createdAt")} {formatDateTime(task.created_at)}
          </span>
        </div>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <TaskStatusBadge status={task.status} announce />
        <StateBadge
          value={pageState}
          tone={PAGE_STATE_TONE[pageState]}
          label={pageStateLabel(pageState)}
        />
        <Badge
          variant="secondary"
          className={cn(
            "gap-1.5 font-normal",
            TONE_TEXT_CLASS[WS_STATUS_TONE[wsStatus]]
          )}
        >
          <span
            aria-hidden
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              TONE_DOT_CLASS[WS_STATUS_TONE[wsStatus]]
            )}
          />
          {t("connection")} {wsStatusLabel(wsStatus)}
        </Badge>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="default"
          size="sm"
          disabled={!isStartable(task.status) || start.isPending}
          onClick={() => start.mutate()}
        >
          {tList("start")}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!isCancellable(task.status) || cancel.isPending}
          onClick={() => cancel.mutate()}
        >
          {tList("cancelTask")}
        </Button>
      </div>
    </header>
  )
}
