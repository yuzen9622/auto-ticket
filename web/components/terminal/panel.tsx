import * as React from "react"

import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

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
    <Card className={cn("flex min-h-0 flex-col", className)}>
      {title !== undefined && (
        <CardHeader className="flex flex-row items-center justify-between space-y-0 px-4 pt-4 pb-2">
          <CardTitle className="text-sm font-semibold">{title}</CardTitle>
          {actions && (
            <div className="flex shrink-0 items-center gap-1">{actions}</div>
          )}
        </CardHeader>
      )}
      <CardContent className={cn("min-h-0 flex-1 p-4", bodyClassName)}>
        {children}
      </CardContent>
    </Card>
  )
}
