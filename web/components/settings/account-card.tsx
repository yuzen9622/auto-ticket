"use client"

import * as React from "react"
import { useQuery } from "@tanstack/react-query"
import { useTranslations } from "next-intl"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getAccountStatus } from "@/lib/api/accounts"
import { PLATFORM, PLATFORM_NAMES, type Platform } from "@/lib/contract"
import { useApiErrorMessage } from "@/lib/i18n/errors"

export interface AccountCardProps {
  platform?: Platform
  onPlatformChange?: (platform: Platform) => void
}

/**
 * 遮罩由後端產生（masked_account），前端直接顯示、不還原、不重組（計畫 §3.3）。
 */
export function AccountCard({
  platform: platformProp,
  onPlatformChange,
}: AccountCardProps) {
  const t = useTranslations("settings")
  const tc = useTranslations("common")
  const apiErrorMessage = useApiErrorMessage()

  const [internalPlatform, setInternalPlatform] =
    React.useState<Platform>("kktix")
  const currentPlatform = platformProp ?? internalPlatform

  const handlePlatformChange = (next: Platform) => {
    if (platformProp === undefined) {
      setInternalPlatform(next)
    }
    onPlatformChange?.(next)
  }

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["account-status", currentPlatform],
    queryFn: () => getAccountStatus(currentPlatform),
  })

  return (
    <Panel
      title={t("accountHeading")}
      actions={
        <Select
          value={currentPlatform}
          onValueChange={(val) => handlePlatformChange(val as Platform)}
        >
          <SelectTrigger className="h-7 w-28 text-xs" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PLATFORM.map((p) => (
              <SelectItem key={p} value={p} className="text-xs">
                {PLATFORM_NAMES[p]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      }
    >
      {isLoading && (
        <p className="text-xs text-muted-foreground">{tc("loading")}…</p>
      )}

      {isError && (
        <p className="text-xs text-destructive">
          {error ? apiErrorMessage(error) : t("loadFailed")}
        </p>
      )}

      {data && (
        <>
          <KvRow
            label={t("platform")}
            value={PLATFORM_NAMES[currentPlatform] ?? data.platform}
          />
          <KvRow label={t("source")} value={data.source} />
          <KvRow
            label={t("configured")}
            value={data.configured ? t("configured") : t("notConfigured")}
            tone={
              data.configured
                ? "text-emerald-600 dark:text-emerald-400 font-medium"
                : "text-amber-600 dark:text-amber-400 font-medium"
            }
          />
          <KvRow
            label={t("maskedAccount")}
            value={data.masked_account ?? "—"}
          />
          {data.credential_kind && (
            <KvRow
              label={t.has("credentialKind") ? t("credentialKind") : "憑證類型"}
              value={data.credential_kind === "cookie" ? "Cookie" : "密碼"}
            />
          )}
        </>
      )}
    </Panel>
  )
}

