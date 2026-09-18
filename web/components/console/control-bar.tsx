"use client"

import * as React from "react"
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
import { purchaseStateLabel } from "@/lib/fsm"
import type { ClientCommand } from "@/lib/ws/types"

export function ControlBar({
  connected,
  onSend,
}: {
  connected: boolean
  onSend: (cmd: Omit<ClientCommand, "task_id">) => boolean
}) {
  const [targetState, setTargetState] =
    React.useState<PurchaseState>("SALE_OPEN")

  const send = (cmd: Omit<ClientCommand, "task_id">) => {
    if (onSend(cmd)) {
      toast.success(`已送出 ${cmd.action}`)
    } else {
      toast.error("WebSocket 未連線，指令未送出")
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-[4px] border border-[var(--oc-border)] bg-[var(--oc-surface)] px-3 py-2">
      <Button
        variant="outline"
        size="sm"
        disabled={!connected}
        onClick={() => send({ action: "PAUSE" })}
      >
        暫停
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={!connected}
        onClick={() => send({ action: "RESUME" })}
      >
        繼續
      </Button>

      <span aria-hidden className="h-5 w-px bg-[var(--oc-border)]" />

      <Label
        htmlFor="target-state"
        className="text-[11px] text-[var(--oc-muted)]"
      >
        強制轉移至
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
              {s} — {purchaseStateLabel(s)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant="accent"
        size="sm"
        disabled={!connected}
        onClick={() =>
          send({ action: "FORCE_TRANSITION", target_state: targetState })
        }
      >
        強制轉移
      </Button>

      <div className="ml-auto flex items-center gap-2">
        {!connected && (
          <span className="text-[11px] text-[var(--oc-muted)]">
            未連線，控制指令不可用
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
