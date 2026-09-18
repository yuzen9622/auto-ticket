"use client"

import { useQuery } from "@tanstack/react-query"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { getAccountStatus } from "@/lib/api/accounts"

/**
 * 遮罩由後端產生（masked_account），前端直接顯示、不還原、不重組（計畫 §3.3）。
 */
export function AccountCard({ platform }: { platform: string }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["account-status", platform],
    queryFn: () => getAccountStatus(platform),
  })

  return (
    <Panel title={`帳號狀態 · ${platform}`}>
      {isLoading && (
        <p className="text-[11px] text-[var(--oc-muted)]">載入中…</p>
      )}

      {isError && (
        <p className="text-[11px] text-[var(--oc-danger)]">
          {error instanceof Error ? error.message : "無法讀取帳號狀態"}
        </p>
      )}

      {data && (
        <>
          <KvRow label="platform" value={data.platform} />
          <KvRow label="source" value={data.source} />
          <KvRow
            label="configured"
            value={data.configured ? "已設定" : "未設定"}
            tone={
              data.configured
                ? "text-[var(--oc-success)]"
                : "text-[var(--oc-warning)]"
            }
          />
          <KvRow label="masked_account" value={data.masked_account ?? "—"} />
        </>
      )}
    </Panel>
  )
}
