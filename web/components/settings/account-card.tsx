"use client"

import { useQuery } from "@tanstack/react-query"
import { useTranslations } from "next-intl"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { getAccountStatus } from "@/lib/api/accounts"
import { useApiErrorMessage } from "@/lib/i18n/errors"

/**
 * 遮罩由後端產生（masked_account），前端直接顯示、不還原、不重組（計畫 §3.3）。
 */
export function AccountCard({ platform }: { platform: string }) {
  const t = useTranslations("settings")
  const tc = useTranslations("common")
  const apiErrorMessage = useApiErrorMessage()
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["account-status", platform],
    queryFn: () => getAccountStatus(platform),
  })

  return (
    <Panel title={`${t("accountHeading")} · ${platform}`}>
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
          <KvRow label={t("platform")} value={data.platform} />
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
        </>
      )}
    </Panel>
  )
}
