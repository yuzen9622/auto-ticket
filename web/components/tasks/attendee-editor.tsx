"use client"

import * as React from "react"
import { Eye, EyeOff, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { AttendeeProfile } from "@/lib/api/types"
import { PHONE_PATTERN } from "@/lib/contract"
import { saveAttendee } from "@/lib/local-profile"

/**
 * 實名制參加人。`id_number` 一律 password 輸入 + 顯示切換，
 * 且**不會**被 saveAttendee 寫進 localStorage（白名單序列化）。
 */
export function AttendeeEditor({
  value,
  onChange,
}: {
  value: AttendeeProfile[]
  onChange: (next: AttendeeProfile[]) => void
}) {
  const [revealed, setRevealed] = React.useState<Set<number>>(new Set())

  const toggleReveal = (i: number) => {
    setRevealed((prev) => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }

  const update = (i: number, patch: Partial<AttendeeProfile>) => {
    onChange(value.map((a, idx) => (idx === i ? { ...a, ...patch } : a)))
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <Label>參加人（實名制活動才需要）</Label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            onChange([...value, { name: "", phone: "", id_number: null }])
          }
        >
          新增參加人
        </Button>
      </div>

      {value.length === 0 && (
        <p className="text-[11px] text-[var(--oc-muted)]">
          未新增任何參加人；非實名制活動可留空。
        </p>
      )}

      {value.map((a, i) => {
        const phoneInvalid = a.phone !== "" && !PHONE_PATTERN.test(a.phone)
        return (
          <div
            key={i}
            className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2 rounded-[4px] border border-[var(--oc-border)] p-2"
          >
            <div className="flex flex-col gap-1">
              <Label className="text-[10px]">姓名</Label>
              <Input
                value={a.name}
                onChange={(e) => update(i, { name: e.target.value })}
                className="h-7"
                autoComplete="off"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label className="text-[10px]">電話</Label>
              <Input
                value={a.phone}
                onChange={(e) => update(i, { phone: e.target.value })}
                aria-invalid={phoneInvalid}
                className="tabular h-7"
                autoComplete="off"
              />
              {phoneInvalid && (
                <span className="text-[10px] text-[var(--oc-danger)]">
                  格式須符合 8–20 碼數字或 + - ( ) 空白
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label className="text-[10px]">身分證字號（選填）</Label>
              <div className="flex gap-1">
                <Input
                  type={revealed.has(i) ? "text" : "password"}
                  value={a.id_number ?? ""}
                  onChange={(e) =>
                    update(i, { id_number: e.target.value || null })
                  }
                  className="tabular h-7"
                  autoComplete="off"
                />
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={
                    revealed.has(i) ? "隱藏身分證字號" : "顯示身分證字號"
                  }
                  onClick={() => toggleReveal(i)}
                >
                  {revealed.has(i) ? <EyeOff /> : <Eye />}
                </Button>
              </div>
              <span className="text-[10px] text-[var(--oc-muted)]">
                只隨本次送出，不會存入瀏覽器
              </span>
            </div>

            <div className="flex gap-1 pb-0.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => saveAttendee(a)}
                title="只儲存姓名與電話到本機"
              >
                記住
              </Button>
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label="刪除參加人"
                onClick={() => onChange(value.filter((_, idx) => idx !== i))}
              >
                <Trash2 />
              </Button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
