"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronsUpDown,
  GripVertical,
  Plus,
  Trash2,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import type { TicketPriority, TicketTypeOut } from "@/lib/api/types"
import { cn } from "cn"

export interface TicketPriorityEditorProps {
  value: TicketPriority[]
  onChange: (next: TicketPriority[]) => void
  ticketNames?: string[]
  availableTickets?: TicketTypeOut[]
}

/** 票種名稱 Combobox：支援搜尋、快速點選票種並自動帶入票價，且已在其他順位選過的項目會被停用。 */
function TicketNameCombobox({
  value,
  price,
  index,
  allPriorities,
  availableTickets = [],
  ticketNames = [],
  onSelectTicket,
}: {
  value?: string | null
  price: number
  index: number
  allPriorities: TicketPriority[]
  availableTickets?: TicketTypeOut[]
  ticketNames?: string[]
  onSelectTicket: (name: string | null, price?: number) => void
}) {
  const [open, setOpen] = React.useState(false)
  const [search, setSearch] = React.useState("")

  // 整理候選票種清單：優先使用 availableTickets，若無則降級使用 ticketNames
  const ticketOptions = React.useMemo(() => {
    if (availableTickets.length > 0) {
      return availableTickets.map((t) => ({
        id: t.id,
        name: t.name,
        price: t.price,
        status: t.status,
      }))
    }
    return ticketNames.map((name, i) => ({
      id: `ticket-name-${i}`,
      name,
      price: undefined,
      status: "AVAILABLE",
    }))
  }, [availableTickets, ticketNames])

  // 其他順位已選擇的票種集合（名稱 + 價格，或純名稱比對）
  const usedTicketKeys = React.useMemo(() => {
    const keys = new Set<string>()
    allPriorities.forEach((p, idx) => {
      if (idx !== index && p.ticket_name_pattern) {
        // 如果有指定票價，則以 name + price 作為唯一鍵，避免同一名稱不同票價被誤擋
        keys.add(`${p.ticket_name_pattern}__${p.price}`)
        // 為了防呆，如果同名只有一種票價或留空，也記錄純名稱
        keys.add(p.ticket_name_pattern)
      }
    })
    return keys
  }, [allPriorities, index])

  const isCustomValue = Boolean(
    value && !ticketOptions.some((opt) => opt.name === value)
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={`priority-name-${index}`}
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="h-8 w-full justify-between px-2.5 font-normal text-sm"
        >
          <span className="truncate">
            {value ? (
              <span className="flex items-center gap-1.5">
                <span>{value}</span>
                {isCustomValue && (
                  <span className="rounded-sm bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                    自訂
                  </span>
                )}
              </span>
            ) : (
              <span className="text-muted-foreground">
                不限票種名稱（依票價比對）
              </span>
            )}
          </span>
          <ChevronsUpDown className="ml-1 size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[min(calc(100vw-2rem),32rem)] p-0"
        align="start"
      >
        <Command>
          <CommandInput
            placeholder="搜尋或輸入票種名稱…"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>
              {search.trim() ? (
                <div className="flex flex-col items-center gap-2 p-2">
                  <span className="text-xs text-muted-foreground">
                    未找到相符票種
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-7 text-xs"
                    onClick={() => {
                      onSelectTicket(search.trim())
                      setOpen(false)
                      setSearch("")
                    }}
                  >
                    使用自訂名稱「{search.trim()}」
                  </Button>
                </div>
              ) : (
                "無可用票種"
              )}
            </CommandEmpty>

            <CommandGroup heading="特殊選項">
              <CommandItem
                value="__ANY_TICKET__ 不限票種"
                onSelect={() => {
                  onSelectTicket(null)
                  setOpen(false)
                  setSearch("")
                }}
                className="flex items-center justify-between"
              >
                <div className="flex flex-col">
                  <span className="font-medium text-foreground">
                    不限票種名稱
                  </span>
                  <span className="text-xs text-muted-foreground">
                    只比對票價，不限制票種名稱
                  </span>
                </div>
                {!value && <Check className="size-4 text-primary" />}
              </CommandItem>
            </CommandGroup>

            {ticketOptions.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup heading="活動票種（選過的項目無法重複選擇）">
                  {ticketOptions.map((opt) => {
                    const keyWithPrice = `${opt.name}__${opt.price}`
                    // 判斷是否已被其他順位選擇
                    const isUsedByOther =
                      usedTicketKeys.has(keyWithPrice) ||
                      (opt.price === undefined && usedTicketKeys.has(opt.name))

                    const isCurrentSelected =
                      value === opt.name &&
                      (opt.price === undefined || opt.price === price)

                    return (
                      <CommandItem
                        key={opt.id}
                        value={`${opt.name} ${opt.price ?? ""}`}
                        disabled={isUsedByOther}
                        onSelect={() => {
                          if (isUsedByOther) return
                          onSelectTicket(opt.name, opt.price)
                          setOpen(false)
                          setSearch("")
                        }}
                        className={cn(
                          "flex items-center justify-between gap-2 py-2",
                          isUsedByOther && "opacity-40 cursor-not-allowed"
                        )}
                      >
                        <div className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate font-medium">
                            {opt.name}
                          </span>
                          {opt.price !== undefined && (
                            <span className="text-xs tabular text-muted-foreground">
                              NT$ {opt.price.toLocaleString()}
                            </span>
                          )}
                        </div>

                        <div className="flex shrink-0 items-center gap-1.5">
                          {isUsedByOther && (
                            <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                              已在其他順位選擇
                            </span>
                          )}
                          {isCurrentSelected && (
                            <Check className="size-4 text-primary" />
                          )}
                        </div>
                      </CommandItem>
                    )
                  })}
                </CommandGroup>
              </>
            )}

            {search.trim() &&
              !ticketOptions.some(
                (opt) => opt.name.toLowerCase() === search.trim().toLowerCase()
              ) && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="自訂輸入">
                    <CommandItem
                      value={`__CUSTOM__ ${search.trim()}`}
                      onSelect={() => {
                        onSelectTicket(search.trim())
                        setOpen(false)
                        setSearch("")
                      }}
                      className="flex items-center justify-between"
                    >
                      <span className="truncate text-xs">
                        使用自訂名稱：
                        <span className="font-semibold text-foreground">
                          {search.trim()}
                        </span>
                      </span>
                      <Plus className="size-3.5 opacity-70" />
                    </CommandItem>
                  </CommandGroup>
                </>
              )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

/** 後端要求 priorities 至少 1 筆（min_length=1），因此最後一筆不可刪。 */
export function TicketPriorityEditor({
  value,
  onChange,
  ticketNames = [],
  availableTickets = [],
}: TicketPriorityEditorProps) {
  const t = useTranslations("taskForm")
  const [draggedIndex, setDraggedIndex] = React.useState<number | null>(null)
  const [dragOverIndex, setDragOverIndex] = React.useState<number | null>(null)

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

  const handleDragStart = (e: React.DragEvent, index: number) => {
    e.dataTransfer.setData("text/plain", index.toString())
    e.dataTransfer.effectAllowed = "move"
    setDraggedIndex(index)
  }

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = "move"
    if (dragOverIndex !== index) {
      setDragOverIndex(index)
    }
  }

  const handleDragEnd = () => {
    setDraggedIndex(null)
    setDragOverIndex(null)
  }

  const handleDrop = (e: React.DragEvent, targetIndex: number) => {
    e.preventDefault()
    const sourceIndexStr = e.dataTransfer.getData("text/plain")
    const sourceIndex = Number(sourceIndexStr)
    if (isNaN(sourceIndex) || sourceIndex === targetIndex) {
      setDraggedIndex(null)
      setDragOverIndex(null)
      return
    }
    const next = [...value]
    const [moved] = next.splice(sourceIndex, 1)
    next.splice(targetIndex, 0, moved)
    onChange(next.map((p, idx) => ({ ...p, priority: idx + 1 })))
    setDraggedIndex(null)
    setDragOverIndex(null)
  }

  return (
    <div className="flex flex-col gap-3">
      {/* 頂部標題與新增按鈕 */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <Label id="priorities-label" className="text-sm font-semibold">
            {t("priorities")}
          </Label>
          <p className="text-xs text-muted-foreground">
            {t("prioritiesHint")} 依序嘗試購買，可拖曳握把調整優先順序。
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 gap-1 text-xs"
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
          <Plus className="size-3.5" />
          {t("addPriority")}
        </Button>
      </div>

      {/* 桌面端對齊表頭 */}
      <div className="hidden grid-cols-[1.75rem_2.5rem_minmax(0,1fr)_6.5rem_auto] items-center gap-2 px-3 text-xs font-medium text-muted-foreground sm:grid">
        <span />
        <span className="text-center">{t("priorityOrder")}</span>
        <span>{t("priorityName")}</span>
        <span className="text-right">{t("priorityPrice")}</span>
        <span className="text-center">操作</span>
      </div>

      {/* 優先順序列表項目 */}
      <div className="flex flex-col gap-2">
        {value.map((p, i) => {
          const isDragging = draggedIndex === i
          const isOver = dragOverIndex === i

          return (
            <div
              key={i}
              draggable
              onDragStart={(e) => handleDragStart(e, i)}
              onDragOver={(e) => handleDragOver(e, i)}
              onDragEnd={handleDragEnd}
              onDrop={(e) => handleDrop(e, i)}
              className={cn(
                "grid grid-cols-1 items-center gap-2 rounded-lg border bg-card/40 p-2.5 shadow-2xs transition-all sm:grid-cols-[1.75rem_2.5rem_minmax(0,1fr)_6.5rem_auto]",
                isDragging && "opacity-40 border-dashed border-primary scale-[0.99]",
                isOver && !isDragging && "ring-2 ring-primary/40 bg-primary/5",
                !isDragging && !isOver && "hover:bg-muted/15"
              )}
            >
              {/* 拖曳握把 */}
              <div
                className="hidden sm:flex items-center justify-center text-muted-foreground/50 hover:text-foreground cursor-grab active:cursor-grabbing p-1 -m-1"
                title="拖曳以重新排序"
              >
                <GripVertical className="size-4" />
              </div>

              {/* 順序（純文字 Badge 展示，非 input） */}
              <div className="flex items-center gap-2 sm:justify-center">
                <span className="text-xs font-medium text-muted-foreground sm:hidden">
                  {t("priorityOrder")}：
                </span>
                <span
                  id={`priority-order-${i}`}
                  className="inline-flex size-6 items-center justify-center rounded-full bg-muted/80 text-xs font-semibold text-foreground/80 tabular"
                  title={`第 ${i + 1} 優先順位`}
                >
                  #{i + 1}
                </span>
              </div>

              {/* 票種名稱 Combobox（唯一可調整） */}
              <div className="flex flex-col gap-1 sm:gap-0">
                <span className="text-xs font-medium text-muted-foreground sm:hidden">
                  {t("priorityName")}：
                </span>
                <TicketNameCombobox
                  value={p.ticket_name_pattern}
                  price={p.price}
                  index={i}
                  allPriorities={value}
                  availableTickets={availableTickets}
                  ticketNames={ticketNames}
                  onSelectTicket={(name, price) => {
                    const patch: Partial<TicketPriority> = {
                      ticket_name_pattern: name,
                    }
                    if (price !== undefined) {
                      patch.price = price
                    }
                    update(i, patch)
                  }}
                />
              </div>

              {/* 票價（純文字展示，非 input，由所選票種決定） */}
              <div className="flex items-center justify-between sm:justify-end gap-1">
                <span className="text-xs font-medium text-muted-foreground sm:hidden">
                  {t("priorityPrice")}：
                </span>
                <span
                  id={`priority-price-${i}`}
                  className="tabular text-xs font-semibold text-foreground"
                  title="由所選票種自動帶入的票價"
                >
                  NT$ {p.price.toLocaleString()}
                </span>
              </div>

              {/* 操作按鈕組 */}
              <div className="flex items-center justify-end gap-1">
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-muted-foreground hover:text-foreground"
                  aria-label={t("movePriorityUp")}
                  title={t("movePriorityUp")}
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                >
                  <ArrowUp className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-muted-foreground hover:text-foreground"
                  aria-label={t("movePriorityDown")}
                  title={t("movePriorityDown")}
                  disabled={i === value.length - 1}
                  onClick={() => move(i, 1)}
                >
                  <ArrowDown className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 text-destructive/70 hover:bg-destructive/10 hover:text-destructive"
                  aria-label={t("removePriority")}
                  title={t("removePriority")}
                  disabled={value.length <= 1}
                  onClick={() => onChange(value.filter((_, idx) => idx !== i))}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
