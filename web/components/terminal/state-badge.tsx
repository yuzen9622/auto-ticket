"use client"

import * as React from "react"

import {
  TONE_DOT_CLASS,
  TONE_TEXT_CLASS,
  purchaseStateLabel,
  purchaseStateTone,
  taskStatusLabel,
  taskStatusTone,
  type SemanticTone,
} from "@/lib/fsm"
import { cn } from "@/lib/utils"

/**
 * 狀態色票。狀態轉移時以 180ms 淡入（`.oc-enter`），受 prefers-reduced-motion 保護。
 * `announce` 開啟時掛 aria-live="polite"：日誌區高頻更新不掛，只在狀態徽章播報。
 */
export function StateBadge({
  value,
  tone,
  label,
  announce = false,
  className,
}: {
  value: string
  tone: SemanticTone
  label?: string
  announce?: boolean
  className?: string
}) {
  return (
    <span
      key={value}
      {...(announce ? { "aria-live": "polite" as const } : {})}
      className={cn(
        "oc-enter inline-flex items-center gap-1.5 rounded-[4px] border border-[var(--oc-border)] px-2 py-0.5 text-[11px] tracking-wider whitespace-nowrap uppercase",
        TONE_TEXT_CLASS[tone],
        className
      )}
    >
      <span
        aria-hidden
        className={cn("size-2 shrink-0 rounded-full", TONE_DOT_CLASS[tone])}
      />
      {label ?? value}
    </span>
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
  return (
    <StateBadge
      value={status}
      tone={taskStatusTone(status)}
      label={`${status} ${taskStatusLabel(status)}`}
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
  return (
    <StateBadge
      value={state}
      tone={purchaseStateTone(state)}
      label={`${state} ${purchaseStateLabel(state)}`}
      announce={announce}
      className={className}
    />
  )
}
