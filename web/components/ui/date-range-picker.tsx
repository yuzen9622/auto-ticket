"use client"

import * as React from "react"
import { format } from "date-fns"
import { CalendarIcon, XIcon } from "lucide-react"
import type { DateRange } from "react-day-picker"

import { cn } from "cn"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export type { DateRange }

export interface DateRangePickerProps {
  date: DateRange | undefined
  onDateChange: (date: DateRange | undefined) => void
  className?: string
  placeholder?: string
}

export function DateRangePicker({
  date,
  onDateChange,
  className,
  placeholder = "選擇日期範圍",
}: DateRangePickerProps) {
  return (
    <div className={cn("grid gap-2", className)}>
      <Popover>
        <PopoverTrigger asChild>
          <Button
            id="date-range"
            variant="outline"
            className={cn(
              "h-9 justify-start text-left font-normal text-sm gap-2",
              !date && "text-muted-foreground"
            )}
          >
            <CalendarIcon className="size-4 shrink-0" />
            <span className="truncate">
              {date?.from ? (
                date.to ? (
                  <>
                    {format(date.from, "yyyy/MM/dd")} -{" "}
                    {format(date.to, "yyyy/MM/dd")}
                  </>
                ) : (
                  format(date.from, "yyyy/MM/dd")
                )
              ) : (
                placeholder
              )}
            </span>
            {date && (
              <span
                role="button"
                tabIndex={0}
                aria-label="清除日期"
                onClick={(e) => {
                  e.stopPropagation()
                  onDateChange(undefined)
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation()
                    onDateChange(undefined)
                  }
                }}
                className="ml-auto inline-flex size-4 items-center justify-center rounded-sm opacity-60 hover:opacity-100 hover:bg-muted"
              >
                <XIcon className="size-3" />
              </span>
            )}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="range"
            defaultMonth={date?.from}
            selected={date}
            onSelect={onDateChange}
            numberOfMonths={2}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}
