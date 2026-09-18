import * as React from "react"

import { cn } from "@/lib/utils"

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
      className={cn(
        "flex items-center justify-between gap-2 py-1 text-xs",
        className
      )}
    >
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span
        className={cn(
          "tabular shrink-0 text-right font-medium text-foreground",
          tone
        )}
      >
        {value}
      </span>
    </div>
  )
}
