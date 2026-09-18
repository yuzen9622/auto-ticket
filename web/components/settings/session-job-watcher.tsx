"use client"

import * as React from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
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
import { ApiError } from "@/lib/api/client"
import type { JobOut } from "@/lib/api/types"
import {
  TONE_BORDER_CLASS,
  TONE_DOT_CLASS,
  TONE_TEXT_CLASS,
  jobStateTone,
} from "@/lib/fsm"
import {
  JOB_PROGRESS_STEPS,
  explainJobError,
  formatElapsed,
  jobKindLabel,
  jobOutcomeTone,
  jobStateHint,
  jobStateLabel,
  jobStepCount,
  summarizeJobResult,
} from "@/lib/session-job"
import { cn } from "@/lib/utils"

const POLL_INTERVAL_MS = 1000

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "操作失敗"
}

/** 三段式進度條：已完成的段落點亮，非終態時最後一段脈動表示仍在跑。 */
function ProgressTrack({ state }: { state: string | null }) {
  const done = state === null ? 0 : jobStepCount(state)
  const live = state !== null && !isTerminalJobState(state)
  const tone = state === null ? "muted" : jobOutcomeTone(state)

  return (
    <div className="flex items-center gap-1" aria-hidden>
      {JOB_PROGRESS_STEPS.map((_, i) => (
        <span
          key={i}
          className={cn(
            "h-1 flex-1 rounded-[2px] transition-colors duration-200 ease-out",
            i < done ? TONE_DOT_CLASS[tone] : "bg-[var(--oc-border)]",
            live && i === done - 1 && "animate-pulse"
          )}
        />
      ))}
    </div>
  )
}

export function SessionJobWatcher({ platform }: { platform: string }) {
  const [jobId, setJobId] = React.useState<string | null>(null)
  const [startedAt, setStartedAt] = React.useState<number | null>(null)
  const [now, setNow] = React.useState(() => Date.now())

  const onAccepted = (job: JobOut) => {
    setJobId(job.job_id)
    setStartedAt(Date.now())
    setNow(Date.now())
    toast.success(`已送出${jobKindLabel(job.kind)}`)
  }

  const check = useMutation({
    mutationFn: () => requestSessionCheck(platform, "live"),
    onSuccess: onAccepted,
    onError: (e) => toast.error(errorMessage(e)),
  })

  const autoLogin = useMutation({
    mutationFn: () => requestLogin(platform, { mode: "auto", profile: "live" }),
    onSuccess: onAccepted,
    onError: (e) => toast.error(errorMessage(e)),
  })

  const manualLogin = useMutation({
    mutationFn: () =>
      requestLogin(platform, { mode: "manual", profile: "live" }),
    onSuccess: onAccepted,
    onError: (e) => toast.error(errorMessage(e)),
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
    if (job.state === "DONE") toast.success(`${label}成功`)
    else if (job.state === "FAILED")
      toast.error(`${label}失敗：${explainJobError(job.error ?? "").title}`)
    else toast.message(`${label}已取消`)
  }, [job])

  const pending =
    check.isPending || autoLogin.isPending || manualLogin.isPending
  const busy = pending || (jobId !== null && !settled)

  const failure =
    job && (job.state === "FAILED" || job.state === "CANCELLED")
      ? explainJobError(job.error ?? job.state)
      : null
  const resultRows = summarizeJobResult(job?.result ?? null)
  const elapsed = startedAt === null ? null : formatElapsed(now - startedAt)

  return (
    <Panel title="Session 與登入">
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => check.mutate()}
          >
            Session Check
          </Button>
          <Button
            variant="accent"
            size="sm"
            disabled={busy}
            onClick={() => autoLogin.mutate()}
          >
            Auto Login
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => manualLogin.mutate()}
          >
            Manual Login
          </Button>
        </div>

        <p className="text-[10px] text-[var(--oc-muted)]">
          三項操作都在 Worker 端開瀏覽器執行，需先完成 `pnpm run setup`（含
          `playwright install chromium`）並啟動 Worker。 Manual Login 另需以
          --no-headless 啟動 Worker 才看得到視窗。
        </p>

        {jobId === null ? (
          <p className="text-[11px] text-[var(--oc-muted)]">
            尚未送出任何工作。
          </p>
        ) : (
          <div className="flex min-w-0 flex-col gap-2 border-t border-[var(--oc-border)] pt-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[12px] font-bold">
                {job ? jobKindLabel(job.kind) : "工作"}
              </span>
              <div className="flex items-center gap-2">
                {elapsed !== null && (
                  <span className="tabular text-[11px] text-[var(--oc-muted)]">
                    {elapsed}
                  </span>
                )}
                {state ? (
                  <StateBadge
                    value={state}
                    tone={jobStateTone(state)}
                    label={`${state} ${jobStateLabel(state)}`}
                    announce
                  />
                ) : (
                  <span className="text-[11px] text-[var(--oc-muted)]">
                    輪詢中…
                  </span>
                )}
              </div>
            </div>

            <ProgressTrack state={state} />

            <p className="text-[11px] text-[var(--oc-muted)]">
              {state === null ? "正在讀取工作狀態…" : jobStateHint(state)}
            </p>

            {failure && (
              <div
                className={cn(
                  "flex flex-col gap-1 rounded-[4px] border px-2 py-1.5",
                  TONE_BORDER_CLASS[jobOutcomeTone(job?.state ?? "FAILED")]
                )}
              >
                <p
                  className={cn(
                    "text-[11px] font-bold",
                    TONE_TEXT_CLASS[jobOutcomeTone(job?.state ?? "FAILED")]
                  )}
                >
                  {failure.title}
                </p>
                {failure.hint && (
                  <p className="text-[11px] break-words text-[var(--oc-muted)]">
                    {failure.hint}
                  </p>
                )}
                {job?.error && job.error !== failure.title && (
                  <p className="text-[10px] break-all text-[var(--oc-muted)]">
                    {job.error}
                  </p>
                )}
              </div>
            )}

            {resultRows.length > 0 && (
              <div className="flex flex-col rounded-[4px] border border-[var(--oc-border)] px-2 py-1">
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

            <KvRow label="job_id" value={jobId} />

            {isError && (
              <p className="text-[11px] text-[var(--oc-danger)]">
                無法讀取工作狀態：
                {error instanceof Error ? error.message : "未知錯誤"}
              </p>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
