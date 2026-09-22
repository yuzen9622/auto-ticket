"use client"

import * as React from "react"

import { Badge } from "@/components/ui/badge"
import {
  TONE_DOT_CLASS,
  TONE_TEXT_CLASS,
  purchaseStateTone,
  taskStatusTone,
  type SemanticTone,
} from "@/lib/fsm"
import {
  usePurchaseStateLabel,
  useTaskStatusLabel,
} from "@/lib/i18n/labels"
import { cn } from "@/lib/utils"

export function StateBadge({
  value,
  tone,
  label,
  announce = false,
  className,
}: {
  value: string
  tone: SemanticTone
  label: string
  announce?: boolean
  className?: string
}) {
  return (
    <Badge
      key={value}
      variant="secondary"
      {...(announce ? { "aria-live": "polite" as const } : {})}
      className={cn(
        "gap-1.5 border-0 font-normal",
        TONE_TEXT_CLASS[tone],
        className
      )}
    >
      <span
        aria-hidden
        className={cn("size-1.5 shrink-0 rounded-full", TONE_DOT_CLASS[tone])}
      />
      {label}
    </Badge>
  )
}

export function TaskStatusBadge({
  status,
  announce,
  className,
}: {
  status: string
  announce?: boolean
  className?: string
}) {
  const label = useTaskStatusLabel()
  return (
    <StateBadge
      value={status}
      tone={taskStatusTone(status)}
      label={label(status)}
      announce={announce}
      className={className}
    />
  )
}

export function PurchaseStateBadge({
  state,
  announce,
  className,
}: {
  state: string
  announce?: boolean
  className?: string
}) {
  const label = usePurchaseStateLabel()
  return (
    <StateBadge
      value={state}
      tone={purchaseStateTone(state)}
      label={label(state)}
      announce={announce}
      className={className}
    />
  )
}
