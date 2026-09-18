"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"

import { EmergencyDialog } from "@/components/console/emergency-dialog"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { PURCHASE_STATE, type PurchaseState } from "@/lib/contract"
import { useClientActionLabel, usePurchaseStateLabel } from "@/lib/i18n/labels"
import type { ClientCommand } from "@/lib/ws/types"

export function ControlBar({
  connected,
  onSend,
}: {
  connected: boolean
  onSend: (cmd: Omit<ClientCommand, "task_id">) => boolean
}) {
  const t = useTranslations("taskConsole")
  const purchaseStateLabel = usePurchaseStateLabel()
  const actionLabel = useClientActionLabel()
  const [targetState, setTargetState] =
    React.useState<PurchaseState>("SALE_OPEN")

  const send = (cmd: Omit<ClientCommand, "task_id">) => {
    if (onSend(cmd)) {
      toast.success(t("commandSent", { action: actionLabel(cmd.action) }))
    } else {
      toast.error(t("commandNotSent"))
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 py-2">
      <Button
        variant="outline"
        size="sm"
        disabled={!connected}
        onClick={() => send({ action: "PAUSE" })}
      >
        {t("pause")}
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={!connected}
        onClick={() => send({ action: "RESUME" })}
      >
        {t("resume")}
      </Button>

      <span aria-hidden className="h-5 w-px bg-border" />

      <Label htmlFor="target-state" className="text-xs text-muted-foreground">
        {t("forceTransitionTo")}
      </Label>
      {/* target_state 只能從 PurchaseState 下拉選取，不接受自由輸入（計畫 §3.4 第 5 點）。 */}
      <Select
        value={targetState}
        onValueChange={(v) => setTargetState(v as PurchaseState)}
      >
        <SelectTrigger
          id="target-state"
          size="sm"
          className="h-6 w-56 text-[11px]"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {PURCHASE_STATE.map((s) => (
            <SelectItem key={s} value={s}>
              {purchaseStateLabel(s)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant="default"
        size="sm"
        disabled={!connected}
        onClick={() =>
          send({ action: "FORCE_TRANSITION", target_state: targetState })
        }
      >
        {t("forceTransition")}
      </Button>

      <div className="ml-auto flex items-center gap-2">
        {!connected && (
          <span className="text-xs text-muted-foreground">
            {t("disconnected")}
          </span>
        )}
        <EmergencyDialog
          disabled={!connected}
          onConfirm={() => send({ action: "EMERGENCY_STOP" })}
        />
      </div>
    </div>
  )
}
