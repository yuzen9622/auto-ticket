"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import Link from "next/link"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CircuitBoard, Copy, MoreHorizontal } from "lucide-react"
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
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/animate-ui/components/radix/dropdown-menu"
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
import { useApiErrorMessage } from "@/lib/i18n/errors"
import { useTaskStatusLabel } from "@/lib/i18n/labels"

export function TaskRowActions({ task }: { task: TaskResponse }) {
  const t = useTranslations("taskList")
  const common = useTranslations("common")
  const apiErrorMessage = useApiErrorMessage()
  const taskStatusLabel = useTaskStatusLabel()
  const [showDeleteDialog, setShowDeleteDialog] = React.useState(false)

  const onError = (error: unknown) =>
    toast.error(
      error instanceof ApiError ? apiErrorMessage(error) : t("actionFailed")
    )

  const qc = useQueryClient()
  const invalidate = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["tasks"] })
  }, [qc])

  const start = useMutation({
    mutationFn: () => startTask(task.id),
    onSuccess: (r) => {
      toast.success(r.triggered ? t("started") : t("queued"))
      invalidate()
    },
    onError,
  })

  const cancel = useMutation({
    mutationFn: () => cancelTask(task.id),
    onSuccess: (r) => {
      // 後端回的是原始狀態，播報前先翻譯。
      toast.success(t("cancelled", { status: taskStatusLabel(r.status) }))
      invalidate()
    },
    onError,
  })

  const remove = useMutation({
    mutationFn: () => deleteTask(task.id),
    onSuccess: () => {
      toast.success(t("deleted"))
      invalidate()
    },
    onError,
  })

  const canDelete = isDeletable(task.status)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="h-8 w-8 p-0">
            <span className="sr-only">Open menu</span>
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuLabel>{common("actions")}</DropdownMenuLabel>
          <DropdownMenuItem
            onClick={() => {
              void navigator.clipboard.writeText(task.id)
              toast.success(common("copied"))
            }}
          >
            <Copy />
            {common("copy")}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem inset>
            <Link href={`/tasks/${task.id}`}>{t("openConsole")}</Link>
          </DropdownMenuItem>
          {isStartable(task.status) && (
            <DropdownMenuItem
              disabled={start.isPending}
              onClick={() => start.mutate()}
            >
              {t("start")}
            </DropdownMenuItem>
          )}
          {isCancellable(task.status) && (
            <DropdownMenuItem
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {t("cancelTask")}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={!canDelete || remove.isPending}
            className="text-destructive focus:text-destructive"
            onClick={() => {
              if (canDelete) setShowDeleteDialog(true)
            }}
          >
            {t("deleteTask")}
          </DropdownMenuItem>
          {!canDelete && (
            <p className="max-w-xs px-2 py-1 text-xs text-muted-foreground">
              {t("deleteBlocked")}
            </p>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("deleteHeading")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("deleteBody", { id: task.id })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{common("cancel")}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                remove.mutate()
                setShowDeleteDialog(false)
              }}
            >
              {t("deleteConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
