"use client"

import { useTranslations } from "next-intl"
import { ExternalLink, ShieldAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { HumanGateLogPayload } from "@/lib/ws/types"

/**
 * Worker 停在需要真人處理的頁面時的提示。
 *
 * 人機驗證本程式不代為通過——它只能把人帶到那一頁。因此這則提示必須夠顯眼、
 * 而且要直接給出可以點開的網址，不能只在日誌裡留一行「請自行確認瀏覽器狀態」。
 */
export function HumanGateBanner({ gate }: { gate: HumanGateLogPayload | null }) {
  const t = useTranslations("taskConsole")

  if (gate === null) return null

  // 兩種死路要分開講，否則會給出錯誤的建議：
  // 人機驗證擋的是瀏覽器本身（開視窗也沒用，得借用自己的 Chrome）；
  // 其他閘門只是沒有視窗可以點。
  const botCheckBlocked =
    gate.page_kind === "CHALLENGE" && gate.can_clear_bot_check === false
  const unattended = !botCheckBlocked && gate.attended === false

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex flex-col gap-2 rounded-md border border-[var(--oc-warning)] bg-[var(--oc-warning)]/5 px-3 py-2"
    >
      <div className="flex items-center gap-2 text-[var(--oc-warning)]">
        <ShieldAlert className="size-4 shrink-0" aria-hidden />
        <span className="text-sm font-bold">
          {botCheckBlocked
            ? t("gateBotCheckTitle")
            : unattended
              ? t("gateUnattendedTitle")
              : t("gateWaitingTitle")}
        </span>
      </div>

      <p className="text-xs leading-relaxed">
        {botCheckBlocked
          ? t("gateBotCheckBody")
          : unattended
            ? t("gateUnattendedBody")
            : gate.hint}
      </p>

      {gate.event_url && !unattended && !botCheckBlocked && (
        <Button asChild size="sm" variant="outline" className="w-fit">
          <a href={gate.event_url} target="_blank" rel="noreferrer">
            <ExternalLink aria-hidden />
            {t("gateOpenPage")}
          </a>
        </Button>
      )}
    </div>
  )
}
