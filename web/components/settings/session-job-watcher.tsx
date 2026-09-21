"use client"

import * as React from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { useTranslations } from "next-intl"
import { toast } from "sonner"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { StateBadge } from "@/components/terminal/state-badge"
import { Button } from "@/components/ui/button"
import {
  getAccountJob,
  isTerminalJobState,
  requestLogin,
  requestSessionCheck,
} from "@/lib/api/accounts"
import type { JobOut } from "@/lib/api/types"
import {
  TONE_BORDER_CLASS,
  TONE_DOT_CLASS,
  TONE_TEXT_CLASS,
  jobStateTone,
} from "@/lib/fsm"
import { useApiErrorMessage } from "@/lib/i18n/errors"
import { useJobStateLabel } from "@/lib/i18n/labels"
import {
  explainJobError,
  formatElapsed,
  jobKindLabel,
  jobOutcomeTone,
  summarizeJobResult,
} from "@/lib/session-job"
import { cn } from "@/lib/utils"

const POLL_INTERVAL_MS = 1000

export function SessionJobWatcher({
  platform,
  profile = "live",
}: {
  platform: string
  profile?: string
}) {
  const t = useTranslations("settings")
  const apiErrorMessage = useApiErrorMessage()
  const jobStateLabel = useJobStateLabel()
  const [jobId, setJobId] = React.useState<string | null>(null)
  const [startedAt, setStartedAt] = React.useState<number | null>(null)
  const [now, setNow] = React.useState(() => Date.now())

  const onAccepted = (job: JobOut) => {
    setJobId(job.job_id)
    setStartedAt(Date.now())
    setNow(Date.now())
    toast.success(t("sessionSubmitted", { kind: jobKindLabel(job.kind) }))
  }

  const check = useMutation({
    mutationFn: () => requestSessionCheck(platform, profile),
    onSuccess: onAccepted,
    onError: (e) => toast.error(apiErrorMessage(e)),
  })

  const autoLogin = useMutation({
    mutationFn: () => requestLogin(platform, { mode: "auto", profile }),
    onSuccess: onAccepted,
    onError: (e) => toast.error(apiErrorMessage(e)),
  })

  const manualLogin = useMutation({
    mutationFn: () =>
      requestLogin(platform, { mode: "manual", profile }),
    onSuccess: onAccepted,
    onError: (e) => toast.error(apiErrorMessage(e)),
  })

  // 每秒輪詢直到 DONE / FAILED / CANCELLED。
  const {
    data: job,
    isError,
    error,
  } = useQuery({
    queryKey: ["account-job", jobId],
    queryFn: () => getAccountJob(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      query.state.data && isTerminalJobState(query.state.data.state)
        ? false
        : POLL_INTERVAL_MS,
  })

  const state = job?.state ?? null
  const settled = state !== null && isTerminalJobState(state)

  // 計時器只在工作仍在進行時跑；終態後停在最終秒數。
  React.useEffect(() => {
    if (startedAt === null || settled) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [startedAt, settled])

  // 終態只提示一次，避免輪詢停止前重複 toast。
  const notifiedRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    if (job === undefined || !isTerminalJobState(job.state)) return
    if (notifiedRef.current === job.job_id) return
    notifiedRef.current = job.job_id
    const label = jobKindLabel(job.kind)
    if (job.state === "DONE")
      toast.success(t("sessionSucceeded", { kind: label }))
    else if (job.state === "FAILED")
      toast.error(
        t("sessionFailed", {
          kind: label,
          reason: explainJobError(job.error ?? "", platform).title,
        })
      )
    else toast.message(t("sessionCancelled", { kind: label }))
  }, [job, t, platform])

  const pending =
    check.isPending || autoLogin.isPending || manualLogin.isPending
  const busy = pending || (jobId !== null && !settled)

  const failure =
    job && (job.state === "FAILED" || job.state === "CANCELLED")
      ? explainJobError(job.error ?? job.state, platform)
      : null
  const resultRows = summarizeJobResult(job?.result ?? null, platform)
  const elapsed = startedAt === null ? null : formatElapsed(now - startedAt)

  const isTixcraft = platform === "tixcraft"
  const isIbon = platform === "ibon"

  const handleAutoLogin = () => {
    autoLogin.mutate()
  }

  return (
    <Panel title={t("sessionHeading")}>
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => check.mutate()}
          >
            {t("sessionCheck")}
          </Button>
          {!isTixcraft && (
            <Button
              variant="default"
              size="sm"
              disabled={busy}
              onClick={handleAutoLogin}
            >
              {t("autoLogin")}
            </Button>
          )}
          <Button
            variant={isTixcraft ? "default" : "outline"}
            size="sm"
            disabled={busy}
            onClick={() => manualLogin.mutate()}
          >
            {t("manualLogin")}
          </Button>
        </div>

        {jobId === null ? (
          <p className="text-xs text-muted-foreground">{t("sessionEmpty")}</p>
        ) : (
          <div className="flex min-w-0 flex-col gap-2 border-t border-border pt-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-semibold">
                {job ? jobKindLabel(job.kind) : t("job")}
              </span>
              <div className="flex items-center gap-2">
                {elapsed !== null && (
                  <span className="tabular text-xs text-muted-foreground">
                    {elapsed}
                  </span>
                )}
                {state ? (
                  <StateBadge
                    value={state}
                    tone={jobStateTone(state)}
                    label={jobStateLabel(state)}
                    announce
                  />
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {t("sessionPolling")}
                  </span>
                )}
              </div>
            </div>

            {failure && (
              <div
                className={cn(
                  "flex flex-col gap-1 rounded-md border px-2.5 py-2",
                  TONE_BORDER_CLASS[jobOutcomeTone(job?.state ?? "FAILED")]
                )}
              >
                <p
                  className={cn(
                    "text-xs font-semibold",
                    TONE_TEXT_CLASS[jobOutcomeTone(job?.state ?? "FAILED")]
                  )}
                >
                  {failure.title}
                </p>
                {failure.hint && (
                  <p className="text-xs break-words text-muted-foreground">
                    {failure.hint}
                  </p>
                )}
                {job?.error && job.error !== failure.title && (
                  <p className="text-xs break-all text-muted-foreground">
                    {job.error}
                  </p>
                )}
              </div>
            )}

            {resultRows.length > 0 && (
              <div className="flex flex-col rounded-md border border-border px-2 py-1">
                {resultRows.map((row) => (
                  <KvRow
                    key={row.label}
                    label={row.label}
                    value={row.value}
                    tone={TONE_TEXT_CLASS[row.tone]}
                  />
                ))}
              </div>
            )}

            {isError && (
              <p className="text-xs text-destructive">
                {t("sessionLoadFailed")}：{error ? apiErrorMessage(error) : ""}
              </p>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
