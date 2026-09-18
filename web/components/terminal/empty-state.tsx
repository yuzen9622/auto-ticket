import * as React from "react"

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
      <p className="text-sm font-medium text-muted-foreground">{message}</p>
      {hint && <p className="max-w-lg text-xs text-muted-foreground">{hint}</p>}
      {action}
    </div>
  )
}
