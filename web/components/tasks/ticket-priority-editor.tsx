"use client"

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
  const update = (i: number, patch: Partial<TicketPriority>) => {
    onChange(value.map((p, idx) => (idx === i ? { ...p, ...patch } : p)))
  }

  const move = (i: number, delta: number) => {
    const target = i + delta
    if (target < 0 || target >= value.length) return
    const next = [...value]
    const [item] = next.splice(i, 1)
    next.splice(target, 0, item)
    // priority 以陣列順序重新編號，讓拖曳／上下移動有一致語意。
    onChange(next.map((p, idx) => ({ ...p, priority: idx + 1 })))
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <Label>票種優先序（至少 1 筆）</Label>
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
          新增一筆
        </Button>
      </div>

      {value.map((p, i) => (
        <div
          key={i}
          className="grid grid-cols-[3rem_minmax(0,1fr)_7rem_auto] items-end gap-2 rounded-[4px] border border-[var(--oc-border)] p-2"
        >
          <div className="flex flex-col gap-1">
            <Label className="text-[10px]">序</Label>
            <Input
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
            <Label className="text-[10px]">票種名稱（子字串或樣式）</Label>
            <Input
              list={`ticket-names-${i}`}
              value={p.ticket_name_pattern ?? ""}
              onChange={(e) =>
                update(i, { ticket_name_pattern: e.target.value || null })
              }
              placeholder="留空表示不限"
              className="h-7"
            />
            <datalist id={`ticket-names-${i}`}>
              {ticketNames.map((n) => (
                <option key={n} value={n} />
              ))}
            </datalist>
          </div>

          <div className="flex flex-col gap-1">
            <Label className="text-[10px]">票價上限</Label>
            <Input
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
              size="icon-sm"
              variant="ghost"
              aria-label="上移"
              disabled={i === 0}
              onClick={() => move(i, -1)}
            >
              <ArrowUp />
            </Button>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label="下移"
              disabled={i === value.length - 1}
              onClick={() => move(i, 1)}
            >
              <ArrowDown />
            </Button>
            <Button
              type="button"
              size="icon-sm"
              variant="ghost"
              aria-label="刪除"
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
