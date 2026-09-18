"use client"

import * as React from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"

import { CopyButton } from "@/components/terminal/copy-button"
import { DataGrid, type Column } from "@/components/terminal/data-grid"
import { EmptyState } from "@/components/terminal/empty-state"
import { Panel } from "@/components/terminal/panel"
import { TaskStatusBadge } from "@/components/terminal/state-badge"
import { TaskRowActions } from "@/components/tasks/task-row-actions"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { listTasks } from "@/lib/api/tasks"
import type { TaskResponse } from "@/lib/api/types"
import { TASK_STATUS } from "@/lib/contract"
import { formatDateTime, shortId } from "@/lib/format"

const PAGE_SIZE = 50
const ALL = "__all__"

function eventTitle(task: TaskResponse): string {
  const v = task.spec["event_title"]
  return typeof v === "string" && v ? v : "—"
}

export function TaskTable() {
  const [status, setStatus] = React.useState<string>(ALL)
  const [offset, setOffset] = React.useState(0)

  // 狀態篩選送後端（非前端過濾），因此換篩選要回第一頁。
  const onStatusChange = (v: string) => {
    setStatus(v)
    setOffset(0)
  }

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["tasks", status, offset],
    queryFn: () =>
      listTasks({
        status: status === ALL ? undefined : status,
        limit: PAGE_SIZE,
        offset,
      }),
    refetchInterval: 5000,
  })

  const columns: Column<TaskResponse>[] = [
    {
      key: "id",
      header: "ID",
      cell: (t) => (
        <span className="flex items-center gap-1">
          <Link href={`/tasks/${t.id}`} className="tabular">
            {shortId(t.id, 16)}
          </Link>
          <CopyButton value={t.id} label="複製任務 ID" />
        </span>
      ),
    },
    {
      key: "title",
      header: "活動",
      cell: (t) => (
        <span className="max-w-[28ch] truncate">{eventTitle(t)}</span>
      ),
      className: "max-w-[28ch]",
    },
    {
      key: "status",
      header: "狀態",
      cell: (t) => <TaskStatusBadge status={t.status} />,
    },
    {
      key: "scheduled",
      header: "排程時間",
      cell: (t) => (
        <span className="tabular">{formatDateTime(t.scheduled_at)}</span>
      ),
    },
    {
      key: "created",
      header: "建立時間",
      cell: (t) => (
        <span className="tabular">{formatDateTime(t.created_at)}</span>
      ),
    },
    {
      key: "actions",
      header: "",
      cell: (t) => <TaskRowActions task={t} />,
      headerClassName: "text-right",
      className: "text-right",
    },
  ]

  const total = data?.total ?? 0

  return (
    <Panel
      title="任務"
      bodyClassName="p-0"
      actions={
        <>
          <Select value={status} onValueChange={onStatusChange}>
            <SelectTrigger size="sm" className="h-6 w-36 text-[11px]">
              <SelectValue placeholder="全部狀態" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>全部狀態</SelectItem>
              {TASK_STATUS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button asChild size="sm" variant="accent">
            <Link href="/tasks/new">建立任務</Link>
          </Button>
        </>
      }
    >
      {isError ? (
        <EmptyState
          message="無法載入任務"
          hint={error instanceof Error ? error.message : undefined}
        />
      ) : isLoading ? (
        <EmptyState message="載入中" />
      ) : (
        <>
          <DataGrid
            columns={columns}
            rows={data?.items ?? []}
            rowKey={(t) => t.id}
            empty={
              <EmptyState
                message="尚無任務"
                hint="從右上角「建立任務」開始一次購票排程。"
              />
            }
          />
          <div className="flex items-center justify-between border-t border-[var(--oc-border)] px-3 py-2 text-[11px] text-[var(--oc-muted)]">
            <span className="tabular">
              {total === 0 ? 0 : offset + 1}–
              {Math.min(offset + PAGE_SIZE, total)} / {total}
            </span>
            <span className="flex gap-1">
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                上一頁
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一頁
              </Button>
            </span>
          </div>
        </>
      )}
    </Panel>
  )
}
