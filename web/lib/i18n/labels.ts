"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

/**
 * 後端 enum → 自然語言的唯一出口。
 *
 * 認不得的值一律顯示「未知狀態」而不是原始字串——多一個後端狀態不該讓使用者
 * 讀到 `WAITING_FOR_SALE`。原始值只留給開發紀錄。
 */
function useEnumLabel(namespace: string) {
  const t = useTranslations(namespace)
  const fallback = useTranslations("status")

  return React.useCallback(
    (value: string | null | undefined): string => {
      if (!value) return fallback("unknown")
      return t.has(value) ? t(value) : fallback("unknown")
    },
    [t, fallback]
  )
}

export function useTaskStatusLabel() {
  return useEnumLabel("status.taskStatus")
}

export function usePurchaseStateLabel() {
  return useEnumLabel("status.purchaseState")
}

export function useJobStateLabel() {
  return useEnumLabel("status.jobState")
}

export function useWsStatusLabel() {
  return useEnumLabel("status.wsStatus")
}

export function usePageStateLabel() {
  return useEnumLabel("status.pageState")
}

export function useEventStatusLabel() {
  return useEnumLabel("status.eventStatus")
}

export function useTicketTypeStatusLabel() {
  return useEnumLabel("status.ticketTypeStatus")
}

export function useSeatStrategyLabel() {
  return useEnumLabel("status.seatStrategy")
}

export function useExecutionModeLabel() {
  return useEnumLabel("status.executionMode")
}

export function useClientActionLabel() {
  return useEnumLabel("status.clientAction")
}

export function useJobKindLabel() {
  return useEnumLabel("status.jobKind")
}
