import * as React from "react"

import { Cursor } from "@/components/terminal/cursor"

export function EmptyState({
  message,
  hint,
  action,
}: {
  message: string
  hint?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
      <p className="text-[12px] text-[var(--oc-muted)]">
        {message}
        <Cursor className="ml-1" />
      </p>
      {hint && (
        <p className="max-w-lg text-[11px] text-[var(--oc-muted)]">{hint}</p>
      )}
      {action}
    </div>
  )
}
