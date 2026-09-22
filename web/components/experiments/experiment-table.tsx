"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { useTranslations } from "next-intl"

import { DataGrid, type Column } from "@/components/terminal/data-grid"
import { EmptyState } from "@/components/terminal/empty-state"
import { PurchaseStateBadge } from "@/components/terminal/state-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/animate-ui/components/radix/dropdown-menu"
import { Input } from "@/components/ui/input"
import { useDebounce } from "@/hooks/use-debounce"
import { listExperiments } from "@/lib/api/experiments"
import type { ExperimentOut } from "@/lib/api/types"
import { formatDateTime, formatMs, shortId } from "@/lib/format"
import { CircuitBoard, Copy, History, MoreHorizontal } from "lucide-react"
import { toast } from "sonner"

const PAGE_SIZE = 50

export function ExperimentTable({ taskId }: { taskId?: string }) {
  const t = useTranslations("history")
  const tc = useTranslations("common")
  const [filter, setFilter] = React.useState(taskId ?? "")
  const debouncedFilter = useDebounce(filter, 300)
  const [offset, setOffset] = React.useState(0)
  const router = useRouter()

  const [prevFilter, setPrevFilter] = React.useState(debouncedFilter)
  if (prevFilter !== debouncedFilter) {
    setPrevFilter(debouncedFilter)
    setOffset(0)
  }

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["experiments", debouncedFilter, offset],
    queryFn: () =>
      listExperiments({
        task_id: debouncedFilter.trim() || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
  })

  const columns: Column<ExperimentOut>[] = [
    {
      key: "id",
      header: "ID",
      cell: (e) => (
        <Link href={`/experiments/${e.id}`} className="tabular">
          {shortId(e.id, 16)}
        </Link>
      ),
    },
    {
      key: "task",
      header: t("columnTask"),
      cell: (e) =>
        e.task_id ? (
          <Link href={`/tasks/${e.task_id}`} className="tabular">
            {shortId(e.task_id, 14)}
          </Link>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      key: "final",
      header: t("columnFinalState"),
      cell: (e) => <PurchaseStateBadge state={e.final_state} />,
    },
    {
      key: "success",
      header: t("columnResult"),
      cell: (e) => (
        <span
          className={
            e.success
              ? "font-medium text-emerald-600 dark:text-emerald-400"
              : "font-medium text-destructive"
          }
        >
          {e.success ? t("success") : t("failure")}
        </span>
      ),
    },
    {
      key: "duration",
      header: t("columnDuration"),
      cell: (e) => (
        <span className="tabular">{formatMs(e.total_duration_ms)}</span>
      ),
    },
    {
      key: "sale_error",
      header: t("columnSaleError"),
      cell: (e) => (
        <span className="tabular">{formatMs(e.sale_time_error_ms)}</span>
      ),
    },
    {
      key: "created",
      header: t("columnCreated"),
      cell: (e) => (
        <span className="tabular">{formatDateTime(e.created_at)}</span>
      ),
    },
    {
      key: "actions",
      header: "",
      cell: (e) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="h-8 w-8 p-0">
              <span className="sr-only">Open menu</span>
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              {tc("actions")}
            </DropdownMenuLabel>
            <DropdownMenuItem
              onSelect={() => router.push(`/experiments/${e.id}`)}
            >
              <History />
              {t("timeline")}
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                void navigator.clipboard.writeText(e.id)
                toast.success(tc("copied"))
              }}
            >
              <Copy />
              {tc("copy")} ID
            </DropdownMenuItem>
            {e.task_id && (
              <DropdownMenuItem
                onSelect={() => router.push(`/tasks/${e.task_id}`)}
              >
                <CircuitBoard />
                {t("columnTask")}
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      ),
      headerClassName: "text-right",
      className: "text-right",
    },
  ]

  const total = data?.total ?? 0

  return (
    <div className="w-full space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-bold tracking-tight">{t("heading")}</h1>
      </div>

      {/* 上方工具列：Debounce 搜尋框 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Input
          value={filter}
          onChange={(ev) => setFilter(ev.target.value)}
          placeholder={t("filterLabel") || "搜尋任務 ID..."}
          className="h-8 w-64 max-w-sm"
          aria-label={t("filterLabel")}
        />
      </div>

      {isError ? (
        <EmptyState
          message={t("loadFailed")}
          hint={error instanceof Error ? error.message : undefined}
        />
      ) : isLoading ? (
        <EmptyState message={tc("loading")} />
      ) : (
        <>
          <DataGrid
            columns={columns}
            rows={data?.items ?? []}
            rowKey={(e) => e.id}
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
                {tc("previousPage")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                {tc("nextPage")}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
