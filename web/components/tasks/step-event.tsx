"use client"

import * as React from "react"
import { useMutation } from "@tanstack/react-query"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError } from "@/lib/api/client"
import { needsOrganizerScope, resolveEvent } from "@/lib/api/events"
import type { EventOut, ResolveEventResponse } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"

export function StepEvent({
  event,
  onResolved,
}: {
  event: EventOut | null
  onResolved: (event: EventOut) => void
}) {
  const [query, setQuery] = React.useState("")
  const [orgs, setOrgs] = React.useState("")
  const [needOrgs, setNeedOrgs] = React.useState(false)
  const [result, setResult] = React.useState<ResolveEventResponse | null>(null)

  const resolve = useMutation({
    mutationFn: (vars: { query: string; orgs: string[] | null }) =>
      resolveEvent({ query: vars.query, orgs: vars.orgs, persist: true }),
    onSuccess: (res) => {
      setResult(res)
      setNeedOrgs(false)
      if (res.auto_selected && res.event) onResolved(res.event)
    },
    onError: (err) => {
      setResult(null)
      // 400 + organizer feed scope 代表需要主辦代號，導引使用者補 orgs 而非顯示通用錯誤。
      if (
        err instanceof ApiError &&
        err.status === 400 &&
        needsOrganizerScope(err.message)
      ) {
        setNeedOrgs(true)
      }
    },
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const orgList = orgs
      .split(",")
      .map((o) => o.trim())
      .filter(Boolean)
    resolve.mutate({
      query: query.trim(),
      orgs: orgList.length ? orgList : null,
    })
  }

  const err = resolve.error

  return (
    <div className="flex flex-col gap-4">
      <Panel title="步驟 1 / 3 — 活動解析">
        <form className="flex flex-col gap-3" onSubmit={submit}>
          <div className="flex flex-col gap-1">
            <Label htmlFor="event-query">活動關鍵字或網址</Label>
            <div className="flex gap-2">
              <Input
                id="event-query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="例如：五月天 2026 演唱會"
                className="h-7"
              />
              <Button
                type="submit"
                size="sm"
                variant="accent"
                disabled={!query.trim() || resolve.isPending}
              >
                {resolve.isPending ? "解析中…" : "解析"}
              </Button>
            </div>
          </div>

          {(needOrgs || orgs) && (
            <div className="flex flex-col gap-1">
              <Label htmlFor="event-orgs">主辦代號（逗號分隔）</Label>
              <Input
                id="event-orgs"
                value={orgs}
                onChange={(e) => setOrgs(e.target.value)}
                placeholder="例如：kktix-organizer-a, kktix-organizer-b"
                className="h-7"
              />
              <span className="text-[10px] text-[var(--oc-warning)]">
                此查詢需要指定主辦 feed 範圍，請補上主辦代號後重試。
              </span>
            </div>
          )}

          {err instanceof ApiError && !needOrgs && (
            <p className="text-[11px] text-[var(--oc-danger)]">
              {err.code === "upstream_failed"
                ? `上游查詢失敗：${err.message}　請稍後重試。`
                : err.message}
            </p>
          )}
        </form>
      </Panel>

      {result && !result.auto_selected && result.candidates.length > 0 && (
        <Panel title="候選活動 — 請選擇一項">
          <ul className="flex flex-col gap-1">
            {result.candidates.map((c) => (
              <li
                key={c.url}
                className="flex items-center justify-between gap-3 rounded-[4px] border border-[var(--oc-border)] px-2 py-1.5"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px]">{c.title}</span>
                  <span className="tabular block truncate text-[10px] text-[var(--oc-muted)]">
                    score {c.score.toFixed(3)} · {c.matched_by} · {c.url}
                  </span>
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => resolve.mutate({ query: c.url, orgs: null })}
                >
                  選擇
                </Button>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {result && !result.auto_selected && result.candidates.length === 0 && (
        <Panel title="查無結果">
          <p className="text-[12px] text-[var(--oc-muted)]">
            沒有找到符合的活動，請換個關鍵字或直接貼上活動網址。
          </p>
        </Panel>
      )}

      {event && (
        <Panel title="已鎖定活動">
          <KvRow label="title" value={event.title} />
          <KvRow label="platform" value={event.platform} />
          <KvRow label="organizer" value={event.organizer} />
          <KvRow label="status" value={event.status} />
          <KvRow label="canonical_url" value={event.canonical_url} />
          <KvRow
            label="sale_start_at"
            value={formatDateTime(event.sale_start_at)}
          />
          <KvRow
            label="ticket_types"
            value={`${event.ticket_types.length} 種`}
          />
        </Panel>
      )}
    </div>
  )
}
