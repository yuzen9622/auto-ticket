"use client"

import * as React from "react"
import Link from "next/link"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ApiError } from "@/lib/api/client"
import {
  cancelTask,
  deleteTask,
  isCancellable,
  isDeletable,
  isStartable,
  startTask,
} from "@/lib/api/tasks"
import type { TaskResponse } from "@/lib/api/types"

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "操作失敗"
}

export function TaskRowActions({ task }: { task: TaskResponse }) {
  const qc = useQueryClient()
  const invalidate = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["tasks"] })
  }, [qc])

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

  const remove = useMutation({
    mutationFn: () => deleteTask(task.id),
    onSuccess: () => {
      toast.success("任務已刪除")
      invalidate()
    },
    onError: (e) => toast.error(errorMessage(e)),
  })

  const canDelete = isDeletable(task.status)

  return (
    <div className="flex items-center justify-end gap-1">
      <Button asChild variant="ghost" size="sm">
        <Link href={`/tasks/${task.id}`}>主控台</Link>
      </Button>

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

      {canDelete ? (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button variant="destructive" size="sm" disabled={remove.isPending}>
              刪除
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>刪除任務？</AlertDialogTitle>
              <AlertDialogDescription>
                將永久刪除任務 {task.id}，此操作無法復原。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction onClick={() => remove.mutate()}>
                確認刪除
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>
            {/* disabled 按鈕不觸發 pointer 事件，用 span 承接 tooltip。 */}
            <span tabIndex={0}>
              <Button variant="destructive" size="sm" disabled>
                刪除
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            狀態 {task.status} 不可刪除；僅 CREATED / CANCELLED / FAILED 可刪。
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}
