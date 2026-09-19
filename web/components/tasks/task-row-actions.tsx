"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { useRouter } from "next/navigation"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  CircuitBoard,
  Copy,
  MoreHorizontal,
  Play,
  Square,
  Trash2,
} from "lucide-react"
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
  const router = useRouter()

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

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="h-8 w-8 p-0">
            <span className="sr-only">Open menu</span>
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuLabel className="text-xs text-muted-foreground">
            {common("actions")}
          </DropdownMenuLabel>

          <DropdownMenuItem onSelect={() => router.push(`/tasks/${task.id}`)}>
            <CircuitBoard />
            {t("openConsole")}
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => {
              void navigator.clipboard.writeText(task.id)
              toast.success(common("copied"))
            }}
          >
            <Copy />
            {common("copy")}
          </DropdownMenuItem>
          {isStartable(task.status) && (
            <DropdownMenuItem
              disabled={start.isPending}
              onClick={() => start.mutate()}
            >
              <Play />
              {t("start")}
            </DropdownMenuItem>
          )}
          {isCancellable(task.status) && (
            <DropdownMenuItem
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              <Square />
              {t("cancelTask")}
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={remove.isPending}
            className="text-destructive focus:text-destructive"
            onClick={() => setShowDeleteDialog(true)}
          >
            <Trash2 />
            {t("deleteTask")}
          </DropdownMenuItem>
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
