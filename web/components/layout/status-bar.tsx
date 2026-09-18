"use client"

import * as React from "react"
import { useTranslations } from "next-intl"
import { useQuery } from "@tanstack/react-query"

import { getHealth, isWorkerOnline } from "@/lib/api/health"
import { relativeTime } from "@/lib/format"
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

/** 相對時間的單位在 `relativeTime` 決定，文案一律取自字典。 */
function useRelativeLabel() {
  const t = useTranslations("statusBar")
  return React.useCallback(
    (value: string | null | undefined, now: number) => {
      const rel = relativeTime(value, now)
      switch (rel.unit) {
        case "never":
          return t("never")
        case "justNow":
          return t("justNow")
        case "seconds":
          return t("secondsAgo", { value: rel.value })
        case "minutes":
          return t("minutesAgo", { value: rel.value })
        case "hours":
          return t("hoursAgo", { value: rel.value })
        default:
          return t("daysAgo", { value: rel.value })
      }
    },
    [t]
  )
}

export function StatusBar() {
  const t = useTranslations("statusBar")
  const common = useTranslations("common")
  const formatRelative = useRelativeLabel()

  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 5000,
  })

  // 相對時間需要隨時鐘前進而重算，不能只依賴 query 的回應。
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(timer)
  }, [])

  const apiTone: SemanticTone = isError ? "danger" : data ? "success" : "muted"
  const workerOnline = data ? isWorkerOnline(data.worker_seen_at, now) : false
  const workerTone: SemanticTone = !data
    ? "muted"
    : workerOnline
      ? "success"
      : "warning"

  return (
    <header className="flex h-9 shrink-0 items-center gap-3 bg-muted/30 px-3 text-xs">

      <span className="flex items-center gap-1.5">
        <Dot tone={apiTone} />
        <span className={TONE_TEXT_CLASS[apiTone]}>
          {t("api")} {isError ? t("apiOffline") : (data?.status ?? "…")}
        </span>
      </span>

      <span className="hidden items-center gap-1.5 sm:flex">
        <Dot tone={data?.broker_ok ? "success" : "muted"} />
        <span className="text-muted-foreground">
          {t("broker")}{" "}
          {data ? (data.broker_ok ? t("brokerOk") : t("brokerDegraded")) : "…"}
        </span>
      </span>

      <span className="flex items-center gap-1.5">
        <Dot tone={workerTone} />
        <span className={TONE_TEXT_CLASS[workerTone]}>
          {data && !workerOnline ? t("workerOffline") : t("worker")}
        </span>
        <span className="tabular text-muted-foreground">
          {data ? formatRelative(data.worker_seen_at, now) : "…"}
        </span>
      </span>

      <span className="ml-auto text-muted-foreground">
        {t("version")} {data?.version ?? common("none")}
      </span>
    </header>
  )
}
