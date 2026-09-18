"use client"

import { Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import type { VerificationRule } from "@/lib/api/types"

/** 只處理主辦自訂的文字問答題；本專案不辨識任何圖形驗證碼。 */
export function VerificationRuleEditor({
  value,
  onChange,
}: {
  value: VerificationRule[]
  onChange: (next: VerificationRule[]) => void
}) {
  const update = (i: number, patch: Partial<VerificationRule>) => {
    onChange(value.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <Label>文字問答題作答規則</Label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            onChange([...value, { pattern: "", answer: "", is_regex: false }])
          }
        >
          新增規則
        </Button>
      </div>

      {value.length === 0 && (
        <p className="text-[11px] text-[var(--oc-muted)]">
          未設定規則；活動若有文字問答題將無法自動作答。
        </p>
      )}

      {value.map((r, i) => (
        <div
          key={i}
          className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] items-end gap-2 rounded-[4px] border border-[var(--oc-border)] p-2"
        >
          <div className="flex flex-col gap-1">
            <Label className="text-[10px]">題幹比對</Label>
            <Input
              value={r.pattern}
              onChange={(e) => update(i, { pattern: e.target.value })}
              className="h-7"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label className="text-[10px]">作答</Label>
            <Input
              value={r.answer}
              onChange={(e) => update(i, { answer: e.target.value })}
              className="h-7"
            />
          </div>

          <div className="flex flex-col gap-1 pb-1">
            <Label className="text-[10px]">正規表示式</Label>
            <Switch
              checked={r.is_regex}
              onCheckedChange={(v) => update(i, { is_regex: v })}
              aria-label="以正規表示式比對"
            />
          </div>

          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label="刪除規則"
            className="mb-0.5"
            onClick={() => onChange(value.filter((_, idx) => idx !== i))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
    </div>
  )
}
