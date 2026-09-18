"use client"

import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/terminal/empty-state"

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <EmptyState
      message="發生未預期的錯誤"
      hint={error.message}
      action={
        <Button variant="outline" size="sm" onClick={reset}>
          重試
        </Button>
      }
    />
  )
}
