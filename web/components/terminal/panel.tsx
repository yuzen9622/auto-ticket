import * as React from "react"

import { cn } from "@/lib/utils"

/** 基礎容器：1px 邊框 + `▌ TITLE` 前綴標題列。零陰影、零圓角（4px 銳角）。 */
export function Panel({
  title,
  actions,
  className,
  bodyClassName,
  children,
}: {
  title?: React.ReactNode
  actions?: React.ReactNode
  className?: string
  bodyClassName?: string
  children: React.ReactNode
}) {
  return (
    <section
      className={cn(
        "flex min-h-0 flex-col rounded-[4px] border border-[var(--oc-border)] bg-[var(--oc-surface)]",
        className
      )}
    >
      {title !== undefined && (
        <header className="flex h-8 shrink-0 items-center justify-between gap-3 border-b border-[var(--oc-border)] px-3">
          <h2 className="truncate text-[11px] font-bold tracking-wider uppercase">
            <span className="text-[var(--oc-accent)]">▌</span> {title}
          </h2>
          {actions && (
            <div className="flex shrink-0 items-center gap-1">{actions}</div>
          )}
        </header>
      )}
      <div className={cn("min-h-0 flex-1 p-3", bodyClassName)}>{children}</div>
    </section>
  )
}
