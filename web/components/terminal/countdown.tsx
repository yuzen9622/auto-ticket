"use client"

import * as React from "react"

import { formatCountdown } from "@/lib/format"
import { cn } from "@/lib/utils"

/** T-60s 內轉橘色警示。 */
const WARNING_WINDOW_MS = 60_000

/**
 * 開賣倒數。數字用 tabular-nums 固定字寬，逐位只在數值變動時淡入，
 * 避免整行重繪造成閃爍；prefers-reduced-motion 下動效自動停用。
 */
export function Countdown({
  timeToSaleMs,
  className,
}: {
  timeToSaleMs: number | null
  className?: string
}) {
  const text = formatCountdown(timeToSaleMs)
  const isWarning =
    timeToSaleMs !== null &&
    timeToSaleMs > 0 &&
    timeToSaleMs <= WARNING_WINDOW_MS
  const isPast = timeToSaleMs !== null && timeToSaleMs <= 0

  return (
    <div
      className={cn(
        "tabular flex items-baseline gap-px text-[22px] leading-none font-bold",
        isWarning && "text-[var(--oc-warning)]",
        isPast && "text-[var(--oc-success)]",
        className
      )}
    >
      {text.split("").map((ch, i) => (
        <span key={`${i}-${ch}`} className="oc-enter inline-block">
          {ch}
        </span>
      ))}
    </div>
  )
}
