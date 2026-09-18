"use client"

import * as React from "react"
import { useQuery } from "@tanstack/react-query"

import { Cursor } from "@/components/terminal/cursor"
import { getHealth, isWorkerOnline } from "@/lib/api/health"
import { formatRelative } from "@/lib/format"
import { TONE_DOT_CLASS, TONE_TEXT_CLASS, type SemanticTone } from "@/lib/fsm"
import { cn } from "@/lib/utils"

function Dot({ tone }: { tone: SemanticTone }) {
  return (
    <span
      aria-hidden
      className={cn("size-2 shrink-0 rounded-full", TONE_DOT_CLASS[tone])}
    />
  )
}

export function StatusBar() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 5000,
  })

  // 相對時間需要隨時鐘前進而重算，不能只依賴 query 的回應。
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(t)
  }, [])

  const apiTone: SemanticTone = isError ? "danger" : data ? "success" : "muted"
  const workerOnline = data ? isWorkerOnline(data.worker_seen_at, now) : false
  const workerTone: SemanticTone = !data
    ? "muted"
    : workerOnline
      ? "success"
      : "warning"

  return (
    <header className="flex h-9 shrink-0 items-center gap-4 border-b border-[var(--oc-border)] bg-[var(--oc-sunken)] px-3 text-[11px]">
      <span className="font-bold tracking-wider">
        auto-ticket
        <Cursor className="ml-1" />
      </span>

      <span className="flex items-center gap-1.5">
        <Dot tone={apiTone} />
        <span className={TONE_TEXT_CLASS[apiTone]}>
          API {isError ? "離線" : (data?.status ?? "…")}
        </span>
      </span>

      <span className="flex items-center gap-1.5">
        <Dot tone={data?.broker_ok ? "success" : "muted"} />
        <span className="text-[var(--oc-muted)]">
          BROKER {data ? (data.broker_ok ? "ok" : "degraded") : "…"}
        </span>
      </span>

      <span className="flex items-center gap-1.5">
        <Dot tone={workerTone} />
        <span className={TONE_TEXT_CLASS[workerTone]}>
          {data && !workerOnline ? "Worker 離線" : "Worker"}
        </span>
        <span className="tabular text-[var(--oc-muted)]">
          {data ? formatRelative(data.worker_seen_at, now) : "…"}
        </span>
      </span>

      <span className="ml-auto text-[var(--oc-muted)]">
        v{data?.version ?? "—"}
      </span>
    </header>
  )
}
