"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

import { ApiError } from "@/lib/api/client"

/** 後端穩定錯誤碼 → 訊息鍵。訊息一律出自字典，不直接顯示後端英文字串。 */
const MESSAGE_KEY_BY_CODE: Record<string, string> = {
  network_error: "network",
  invalid_base_url: "network",
  not_found: "notFound",
  invalid_request: "invalidRequest",
  conflict: "conflict",
  upstream_failed: "upstreamFailed",
  vault_locked: "vaultLocked",
  unsupported: "unsupported",
}

export function useApiErrorMessage() {
  const t = useTranslations("errors")

  return React.useCallback(
    (error: unknown): string => {
      if (error instanceof ApiError) {
        return t(MESSAGE_KEY_BY_CODE[error.code] ?? "unknown")
      }
      return t("unknown")
    },
    [t]
  )
}

/** 後端在正式模式缺少帳號設定時，用 details.reason 標記，讓 UI 能導到設定頁。 */
export function isAccountNotConfigured(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.details !== null &&
    error.details.reason === "account_not_configured"
  )
}
