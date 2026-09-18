"use client"

import * as React from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"

import { DataGrid, type Column } from "@/components/terminal/data-grid"
import { EmptyState } from "@/components/terminal/empty-state"
import { Panel } from "@/components/terminal/panel"
import { PurchaseStateBadge } from "@/components/terminal/state-badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { listExperiments } from "@/lib/api/experiments"
import type { ExperimentOut } from "@/lib/api/types"
import { formatDateTime, formatMs, shortId } from "@/lib/format"

const PAGE_SIZE = 50

export function ExperimentTable({ taskId }: { taskId?: string }) {
  const [filter, setFilter] = React.useState(taskId ?? "")
  const [applied, setApplied] = React.useState(taskId ?? "")
  const [offset, setOffset] = React.useState(0)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["experiments", applied, offset],
    queryFn: () =>
      listExperiments({
        task_id: applied || undefined,
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
      header: "任務",
      cell: (e) =>
        e.task_id ? (
          <Link href={`/tasks/${e.task_id}`} className="tabular">
            {shortId(e.task_id, 14)}
          </Link>
        ) : (
          <span className="text-[var(--oc-muted)]">—</span>
        ),
    },
    {
      key: "final",
      header: "終態",
      cell: (e) => <PurchaseStateBadge state={e.final_state} />,
    },
    {
      key: "success",
      header: "結果",
      cell: (e) => (
        <span
          className={
            e.success ? "text-[var(--oc-success)]" : "text-[var(--oc-danger)]"
          }
        >
          {e.success ? "成功" : "失敗"}
        </span>
      ),
    },
    {
      key: "duration",
      header: "總耗時",
      cell: (e) => (
        <span className="tabular">{formatMs(e.total_duration_ms)}</span>
      ),
    },
    {
      key: "sale_error",
      header: "開賣誤差",
      cell: (e) => (
        <span className="tabular">{formatMs(e.sale_time_error_ms)}</span>
      ),
    },
    { key: "strategy", header: "策略", cell: (e) => e.strategy_used },
    { key: "clock", header: "時鐘同步", cell: (e) => e.clock_sync_mode },
    {
      key: "created",
      header: "建立時間",
      cell: (e) => (
        <span className="tabular">{formatDateTime(e.created_at)}</span>
      ),
    },
  ]

  const total = data?.total ?? 0

  return (
    <Panel
      title="實驗紀錄"
      bodyClassName="p-0"
      actions={
        <form
          className="flex items-center gap-1"
          onSubmit={(ev) => {
            ev.preventDefault()
            setApplied(filter.trim())
            setOffset(0)
          }}
        >
          <Input
            value={filter}
            onChange={(ev) => setFilter(ev.target.value)}
            placeholder="以 task_id 篩選"
            className="h-6 w-52 text-[11px]"
            aria-label="以 task_id 篩選"
          />
          <Button size="sm" variant="outline" type="submit">
            套用
          </Button>
        </form>
      }
    >
      {isError ? (
        <EmptyState
          message="無法載入實驗紀錄"
          hint={error instanceof Error ? error.message : undefined}
        />
      ) : isLoading ? (
        <EmptyState message="載入中" />
      ) : (
        <>
          <DataGrid
            columns={columns}
            rows={data?.items ?? []}
            rowKey={(e) => e.id}
            empty={
              <EmptyState message="尚無實驗紀錄" hint="任務執行後才會產生。" />
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
