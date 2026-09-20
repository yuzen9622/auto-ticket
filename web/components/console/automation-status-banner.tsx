"use client"

import { useTranslations } from "next-intl"
import { CheckCircle2, Loader2, ShieldCheck } from "lucide-react"

import type { AutomationStatus } from "@/lib/ws/use-task-socket"

export function AutomationStatusBanner({
  status,
}: {
  status: AutomationStatus | null
}) {
  const t = useTranslations("taskConsole")

  if (status === null) return null

  const isCompleted = status.phase === "verification_completed"
  const isCloudflare = status.phase === "cloudflare_grace"
  const isOcr = status.phase === "ocr_processing"

  const borderColor = isCompleted
    ? "border-[var(--oc-success)] bg-[var(--oc-success)]/5"
    : "border-[var(--oc-info)] bg-[var(--oc-info)]/5"
  const textColor = isCompleted
    ? "text-[var(--oc-success)]"
    : "text-[var(--oc-info)]"

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex flex-col gap-2 rounded-md border ${borderColor} px-3 py-2`}
    >
      <div className={`flex items-center gap-2 ${textColor}`}>
        {isCompleted ? (
          <CheckCircle2 className="size-4 shrink-0" aria-hidden />
        ) : isCloudflare ? (
          <ShieldCheck className="size-4 shrink-0" aria-hidden />
        ) : (
          <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
        )}
        <span className="text-sm font-bold">
          {isCloudflare
            ? t("autoCloudflareTitle")
            : isOcr
              ? t("autoOcrTitle", {
                  attempt: status.attempt,
                  max: status.max_retries,
                })
              : t("autoVerificationDone")}
        </span>
      </div>

      {isCloudflare && (
        <>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("autoCloudflareBody")}
          </p>
          <span className="text-xs font-mono text-muted-foreground">
            {t("autoCloudflareProgress", {
              elapsed: status.elapsed_s,
              budget: status.budget_s,
              round: status.round,
              maxRounds: status.max_rounds,
            })}
          </span>
        </>
      )}

      {isOcr && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("autoOcrBody")}
        </p>
      )}
    </div>
  )
}
