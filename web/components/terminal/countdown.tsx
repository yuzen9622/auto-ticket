"use client"

import * as React from "react"

import { formatCountdown } from "@/lib/format"
import { cn } from "@/lib/utils"

/** 進入最後一分鐘就轉為警示色。 */
const WARNING_WINDOW_MS = 60_000

export function Countdown({
  remainingMs,
  className,
}: {
  remainingMs: number | null
  className?: string
}) {
  const warning =
    remainingMs !== null && remainingMs > 0 && remainingMs <= WARNING_WINDOW_MS
  const text = formatCountdown(remainingMs)

  return (
    <span
      className={cn(
        "tabular block text-2xl font-bold tracking-tight",
        remainingMs === 0
          ? "text-muted-foreground"
          : warning
            ? "text-amber-500"
            : "text-foreground",
        className
      )}
    >
      {text}
    </span>
  )
}
