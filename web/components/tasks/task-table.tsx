"use client"

import * as React from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { useQuery } from "@tanstack/react-query"

import { DataGrid, type Column } from "@/components/terminal/data-grid"
import { EmptyState } from "@/components/terminal/empty-state"
import { TaskStatusBadge } from "@/components/terminal/state-badge"
import { TaskRowActions } from "@/components/tasks/task-row-actions"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useDebounce } from "@/hooks/use-debounce"
import { listTasks } from "@/lib/api/tasks"
import type { TaskResponse } from "@/lib/api/types"
import { TASK_STATUS } from "@/lib/contract"
import { formatDateTime, shortId } from "@/lib/format"
import { useApiErrorMessage } from "@/lib/i18n/errors"
import { useExecutionModeLabel, useTaskStatusLabel } from "@/lib/i18n/labels"

const PAGE_SIZE = 50
/** 「全部狀態」只是 UI 的哨兵值，不會送給 API。 */
const ALL = "__all__"

export function TaskTable() {
  const t = useTranslations("taskList")
  const common = useTranslations("common")
  const apiErrorMessage = useApiErrorMessage()
  const taskStatusLabel = useTaskStatusLabel()
  const executionModeLabel = useExecutionModeLabel()

  const [status, setStatus] = React.useState<string>(ALL)
  const [offset, setOffset] = React.useState(0)
  const [searchTerm, setSearchTerm] = React.useState("")
  const debouncedSearch = useDebounce(searchTerm, 300)

  // 狀態篩選送後端（非前端過濾），因此換篩選要回第一頁。
  const onStatusChange = (v: string) => {
    setStatus(v)
    setOffset(0)
  }

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["tasks", status, offset],
    queryFn: () =>
      listTasks({
        // 送給 API 的仍是後端認得的原始值；畫面上顯示的是翻譯。
        status: status === ALL ? undefined : status,
        limit: PAGE_SIZE,
        offset,
      }),
    refetchInterval: 5000,
  })

  const eventTitle = React.useCallback(
    (task: TaskResponse): string => {
      const value = task.spec["event_title"]
      return typeof value === "string" && value ? value : common("none")
    },
    [common]
  )

  const columns: Column<TaskResponse>[] = [
    {
      key: "id",
      header: t("columnId"),
      cell: (task) => (
        <span className="flex items-center gap-1">
          <Link href={`/tasks/${task.id}`} className="tabular">
            {shortId(task.id, 16)}
          </Link>
        </span>
      ),
    },
    {
      key: "title",
      header: t("columnEvent"),
      cell: (task) => (
        <span className="block max-w-[28ch] truncate" title={eventTitle(task)}>
          {eventTitle(task)}
        </span>
      ),
      className: "max-w-[28ch] overflow-hidden",
    },
    {
      key: "status",
      header: t("columnStatus"),
      cell: (task) => <TaskStatusBadge status={task.status} />,
    },
    {
      key: "mode",
      header: t("columnMode"),
      cell: (task) => <span>{executionModeLabel(task.execution_mode)}</span>,
    },
    {
      key: "scheduled",
      header: t("columnScheduled"),
      cell: (task) => (
        <span className="tabular">{formatDateTime(task.scheduled_at)}</span>
      ),
    },
    {
      key: "created",
      header: t("columnCreated"),
      cell: (task) => (
        <span className="tabular">{formatDateTime(task.created_at)}</span>
      ),
    },
    {
      key: "actions",
      header: "",
      cell: (task) => <TaskRowActions task={task} />,
      headerClassName: "text-right",
      className: "text-right",
    },
  ]

  const total = data?.total ?? 0

  const filteredRows = React.useMemo(() => {
    const items = data?.items ?? []
    const q = debouncedSearch.trim().toLowerCase()
    if (!q) return items
    return items.filter(
      (task) =>
        task.id.toLowerCase().includes(q) ||
        eventTitle(task).toLowerCase().includes(q)
    )
  }, [data?.items, debouncedSearch, eventTitle])

  return (
    <div className="w-full space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-bold tracking-tight">{t("heading")}</h1>
      </div>

      {/* 上方工具列：Debounce 搜尋框 + 狀態篩選與操作按鈕 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Input
          placeholder="搜尋任務 ID 或活動名稱..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="h-8 w-64 max-w-sm"
        />

        <div className="flex items-center gap-2">
          <Select value={status} onValueChange={onStatusChange}>
            <SelectTrigger
              aria-label={t("filterLabel")}
              size="sm"
              className="h-8 w-36 text-xs"
            >
              <SelectValue placeholder={t("filterAll")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("filterAll")}</SelectItem>
              {TASK_STATUS.map((value) => (
                <SelectItem key={value} value={value}>
                  {taskStatusLabel(value)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button asChild size="sm" variant="default" className="h-8">
            <Link href="/">{t("newTask")}</Link>
          </Button>
        </div>
      </div>

      {isError ? (
        <EmptyState message={t("loadFailed")} hint={apiErrorMessage(error)} />
      ) : isLoading ? (
        <EmptyState message={common("loading")} />
      ) : (
        <>
          <DataGrid
            columns={columns}
            rows={filteredRows}
            rowKey={(task) => task.id}
            empty={<EmptyState message={t("empty")} hint={t("emptyHint")} />}
          />
          {/* 下方分頁與筆數切換：完全對齊 shadcn DataTable 規範 (截圖 5) */}
          <div className="flex items-center justify-between py-2">
            <div className="text-sm text-muted-foreground">
              {total === 0
                ? "共 0 筆資料"
                : `第 ${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} 筆 / 共 ${total} 筆`}
            </div>
            <div className="flex items-center space-x-2">
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                {common("previousPage")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                {common("nextPage")}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
