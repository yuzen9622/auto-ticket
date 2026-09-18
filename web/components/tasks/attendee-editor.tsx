"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { Eye, EyeOff, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { AttendeeProfile } from "@/lib/api/types"
import { saveAttendee } from "@/lib/local-profile"

/**
 * 實名制參加者。身分識別碼一律以密碼欄位輸入 ＋ 顯示切換，
 * 且**不會**被 saveAttendee 寫進 localStorage（白名單序列化）。
 */
export function AttendeeEditor({
  value,
  onChange,
  errors,
}: {
  value: AttendeeProfile[]
  onChange: (next: AttendeeProfile[]) => void
  errors?: Record<string, string>
}) {
  const t = useTranslations("taskForm")
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
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>{t("attendeeSection")}</Label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            onChange([...value, { name: "", phone: "", id_number: null }])
          }
        >
          {t("addAttendee")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("attendeeHint")}</p>

      {value.length === 0 && (
        <p className="text-xs text-muted-foreground">{t("noAttendees")}</p>
      )}

      {value.map((a, i) => {
        const nameError = errors?.[`attendee.${i}.name`]
        const phoneError = errors?.[`attendee.${i}.phone`]
        return (
          <div
            key={i}
            className="grid grid-cols-1 items-end gap-2 rounded-md bg-muted/20 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
          >
            <div className="flex flex-col gap-1">
              <Label htmlFor={`attendee-name-${i}`} className="text-[10px]">
                {t("attendeeName")}
              </Label>
              <Input
                id={`attendee-name-${i}`}
                value={a.name}
                onChange={(e) => update(i, { name: e.target.value })}
                aria-invalid={nameError !== undefined}
                aria-describedby={
                  nameError ? `attendee-name-error-${i}` : undefined
                }
                className="h-7"
                autoComplete="off"
              />
              {nameError && (
                <span
                  id={`attendee-name-error-${i}`}
                  className="text-xs text-destructive"
                >
                  {nameError}
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor={`attendee-phone-${i}`} className="text-[10px]">
                {t("attendeePhone")}
              </Label>
              <Input
                id={`attendee-phone-${i}`}
                value={a.phone}
                onChange={(e) => update(i, { phone: e.target.value })}
                aria-invalid={phoneError !== undefined}
                aria-describedby={
                  phoneError ? `attendee-phone-error-${i}` : undefined
                }
                className="tabular h-7"
                autoComplete="off"
              />
              {phoneError && (
                <span
                  id={`attendee-phone-error-${i}`}
                  className="text-xs text-destructive"
                >
                  {phoneError}
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor={`attendee-id-${i}`} className="text-[10px]">
                {t("attendeeIdNumber")}
              </Label>
              <div className="flex gap-1">
                <Input
                  id={`attendee-id-${i}`}
                  type={revealed.has(i) ? "text" : "password"}
                  value={a.id_number ?? ""}
                  onChange={(e) =>
                    update(i, { id_number: e.target.value || null })
                  }
                  aria-describedby={`attendee-id-hint-${i}`}
                  className="tabular h-7"
                  autoComplete="off"
                />
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  aria-label={revealed.has(i) ? t("hideValue") : t("showValue")}
                  onClick={() => toggleReveal(i)}
                >
                  {revealed.has(i) ? <EyeOff /> : <Eye />}
                </Button>
              </div>
              <span
                id={`attendee-id-hint-${i}`}
                className="text-xs text-muted-foreground"
              >
                {t("attendeeIdNumberHint")}
              </span>
            </div>

            <div className="flex gap-1 pb-0.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => saveAttendee(a)}
              >
                {t("rememberAttendee")}
              </Button>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={t("removeAttendee")}
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
