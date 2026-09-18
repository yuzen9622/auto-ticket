import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * `key ······ value` 點線對齊列。
 * 點線用 border-b-dotted 撐開，等寬字下 key/value 兩端永遠對齊。
 */
export function KvRow({
  label,
  value,
  tone,
  className,
}: {
  label: React.ReactNode
  value: React.ReactNode
  tone?: string
  className?: string
}) {
  return (
    <div
      className={cn("flex items-baseline gap-2 py-1 text-[12px]", className)}
    >
      <span className="shrink-0 text-[var(--oc-muted)]">{label}</span>
      <span
        aria-hidden
        className="min-w-4 flex-1 translate-y-[-3px] border-b border-dotted border-[var(--oc-border)]"
      />
      <span className={cn("tabular shrink-0 text-right", tone)}>{value}</span>
    </div>
  )
}
