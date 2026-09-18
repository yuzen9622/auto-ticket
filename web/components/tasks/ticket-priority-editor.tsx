"use client"

import { useTranslations } from "next-intl"
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { TicketPriority } from "@/lib/api/types"

/** 後端要求 priorities 至少 1 筆（min_length=1），因此最後一筆不可刪。 */
export function TicketPriorityEditor({
  value,
  onChange,
  ticketNames,
}: {
  value: TicketPriority[]
  onChange: (next: TicketPriority[]) => void
  ticketNames: string[]
}) {
  const t = useTranslations("taskForm")

  const update = (i: number, patch: Partial<TicketPriority>) => {
    onChange(value.map((p, idx) => (idx === i ? { ...p, ...patch } : p)))
  }

  const move = (i: number, delta: number) => {
    const target = i + delta
    if (target < 0 || target >= value.length) return
    const next = [...value]
    const [item] = next.splice(i, 1)
    next.splice(target, 0, item)
    // priority 以陣列順序重新編號，讓上下移動有一致語意。
    onChange(next.map((p, idx) => ({ ...p, priority: idx + 1 })))
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label id="priorities-label">{t("priorities")}</Label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            onChange([
              ...value,
              {
                price: 0,
                ticket_name_pattern: null,
                priority: value.length + 1,
              },
            ])
          }
        >
          {t("addPriority")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("prioritiesHint")}</p>

      {value.map((p, i) => (
        <div
          key={i}
          className="grid grid-cols-1 items-end gap-2 rounded-md bg-muted/20 p-3 sm:grid-cols-[3.5rem_minmax(0,1fr)_7rem_auto]"
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor={`priority-order-${i}`} className="text-[10px]">
              {t("priorityOrder")}
            </Label>
            <Input
              id={`priority-order-${i}`}
              type="number"
              min={1}
              value={p.priority}
              onChange={(e) =>
                update(i, { priority: Math.max(1, Number(e.target.value)) })
              }
              className="tabular h-7"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor={`priority-name-${i}`} className="text-[10px]">
              {t("priorityName")}
            </Label>
            <Input
              id={`priority-name-${i}`}
              list={`ticket-names-${i}`}
              value={p.ticket_name_pattern ?? ""}
              onChange={(e) =>
                update(i, { ticket_name_pattern: e.target.value || null })
              }
              aria-describedby={`priority-name-hint-${i}`}
              className="h-7"
            />
            <span
              id={`priority-name-hint-${i}`}
              className="text-xs text-muted-foreground"
            >
              {t("priorityNameHint")}
            </span>
            <datalist id={`ticket-names-${i}`}>
              {ticketNames.map((n) => (
                <option key={n} value={n} />
              ))}
            </datalist>
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor={`priority-price-${i}`} className="text-[10px]">
              {t("priorityPrice")}
            </Label>
            <Input
              id={`priority-price-${i}`}
              type="number"
              min={0}
              value={p.price}
              onChange={(e) =>
                update(i, { price: Math.max(0, Number(e.target.value)) })
              }
              className="tabular h-7"
            />
          </div>

          <div className="flex gap-1 pb-0.5">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={t("movePriorityUp")}
              disabled={i === 0}
              onClick={() => move(i, -1)}
            >
              <ArrowUp />
            </Button>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={t("movePriorityDown")}
              disabled={i === value.length - 1}
              onClick={() => move(i, 1)}
            >
              <ArrowDown />
            </Button>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={t("removePriority")}
              disabled={value.length <= 1}
              onClick={() => onChange(value.filter((_, idx) => idx !== i))}
            >
              <Trash2 />
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
