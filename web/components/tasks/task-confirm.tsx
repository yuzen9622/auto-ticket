"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

import { EventSummary } from "@/components/tasks/event-summary"
import { Panel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import type { EventOut } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"
import { useExecutionModeLabel, useSeatStrategyLabel } from "@/lib/i18n/labels"
import { maskEmail, maskIdNumber, maskPhone } from "@/lib/mask"
import type { TaskDraft } from "@/lib/task-draft"

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 sm:flex-row sm:gap-2">
      <dt className="shrink-0 text-muted-foreground sm:w-40">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  )
}

/**
 * 送出前的確認畫面。
 *
 * 每一列都用自然語言標題；程式欄位名不出現在畫面上。敏感欄位一律遮蔽顯示。
 */
export function TaskConfirm({
  event,
  draft,
  sellingNow,
  submitting,
  onBack,
  onSubmit,
}: {
  event: EventOut
  draft: TaskDraft
  /** 活動已經在販售：這筆任務送出後立即執行，沒有搶票時間可顯示。 */
  sellingNow: boolean
  submitting: boolean
  onBack: () => void
  onSubmit: () => void
}) {
  const t = useTranslations("taskForm")
  const tEvent = useTranslations("event")
  const common = useTranslations("common")
  const seatStrategyLabel = useSeatStrategyLabel()
  const executionModeLabel = useExecutionModeLabel()

  const tp = draft.ticket_preference
  const contact = draft.contact_profile
  const ticketingTime = sellingNow
    ? t("startImmediately")
    : draft.ticketing_time_local
      ? new Date(draft.ticketing_time_local).toLocaleString("zh-TW", {
          hour12: false,
        })
      : common("none")

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <EventSummary event={event} />

      <Panel title={t("confirmHeading")}>
        <div className="flex flex-col gap-3">
          <p className="text-xs text-muted-foreground">
            {t("confirmHint")}　{t("maskedNotice")}
          </p>

          <dl className="flex flex-col gap-1 text-[11px]">
            <Row
              label={sellingNow ? t("startTiming") : t("ticketingTime")}
              value={ticketingTime}
            />
            <Row
              label={t("originalSaleStart")}
              value={
                event.sale_start_at
                  ? formatDateTime(event.sale_start_at)
                  : common("none")
              }
            />
            <Row label={t("quantity")} value={tp.quantity} />
            <Row
              label={t("priorities")}
              value={
                tp.priorities.length === 0 ? (
                  t("prioritiesNoneUseRule")
                ) : (
                  <span className="flex flex-col gap-0.5">
                    {tp.priorities.map((priority, index) => (
                      <span key={index}>
                        {index + 1}.{" "}
                        {priority.ticket_name_pattern ?? t("priorityNameHint")}
                      </span>
                    ))}
                  </span>
                )
              }
            />
            <Row
              label={t("rule")}
              value={
                tp.rule === null ? (
                  common("none")
                ) : (
                  <span className="flex flex-col gap-0.5">
                    <span>
                      {t("ruleMaxPrice")}：
                      {tp.rule.max_price === null
                        ? t("ruleNoLimit")
                        : `NT$ ${tp.rule.max_price.toLocaleString()}`}
                      　{t("ruleMinPrice")}：
                      {tp.rule.min_price === null
                        ? t("ruleNoLimit")
                        : `NT$ ${tp.rule.min_price.toLocaleString()}`}
                    </span>
                    <span>
                      {t("rulePriceOrder")}：
                      {t(`rulePriceOrder_${tp.rule.price_order}`)}
                    </span>
                    {tp.rule.prefer_name_patterns.length > 0 && (
                      <span>
                        {t("rulePrefer")}：
                        {tp.rule.prefer_name_patterns.join("、")}
                      </span>
                    )}
                    {tp.rule.exclude_name_patterns.length > 0 && (
                      <span>
                        {t("ruleExclude")}：
                        {tp.rule.exclude_name_patterns.join("、")}
                      </span>
                    )}
                  </span>
                )
              }
            />
            <Row
              label={t("fallbackToAny")}
              value={tp.fallback_to_any ? common("yes") : common("no")}
            />
            <Row
              label={t("seatStrategy")}
              value={seatStrategyLabel(tp.seat_preference.strategy)}
            />
            <Row
              label={t("adjacent")}
              value={tp.seat_preference.adjacent ? common("yes") : common("no")}
            />
            {tp.seat_preference.strategy === "specific_zone" && (
              <Row
                label={t("preferredZones")}
                value={tp.seat_preference.preferred_zones.join("、")}
              />
            )}
            <Row
              label={t("contactSummary")}
              value={`${contact.name} · ${maskPhone(contact.phone)} · ${maskEmail(
                contact.email
              )}`}
            />
            <Row
              label={t("attendeeSummary")}
              value={
                draft.attendees.length === 0 ? (
                  t("noAttendeeSummary")
                ) : (
                  <span className="flex flex-col gap-0.5">
                    {draft.attendees.map((attendee, index) => (
                      <span key={index}>
                        {attendee.name} · {maskPhone(attendee.phone)}
                        {attendee.id_number
                          ? ` · ${maskIdNumber(attendee.id_number)}`
                          : ""}
                      </span>
                    ))}
                  </span>
                )
              }
            />
            <Row
              label={t("verificationSummary")}
              value={
                draft.verification_rules.length === 0 ? (
                  t("noVerificationSummary")
                ) : (
                  <span className="flex flex-col gap-0.5">
                    {draft.verification_rules.map((rule, index) => (
                      <span key={index}>
                        {rule.pattern} → {rule.answer}
                      </span>
                    ))}
                  </span>
                )
              }
            />
            <Row
              label={t("qualificationCode")}
              value={
                draft.qualification_code.trim() === ""
                  ? common("none")
                  : maskIdNumber(draft.qualification_code)
              }
            />
            <Row label={t("maxRetries")} value={draft.max_retries} />
            <Row label={t("timeoutSeconds")} value={draft.timeout_seconds} />
            <Row
              label={t("autoLogin")}
              value={draft.auto_login ? common("yes") : common("no")}
            />
            <Row
              label={t("executionMode")}
              value={executionModeLabel(draft.execution_mode)}
            />
            <Row label={tEvent("eventUrl")} value={event.canonical_url} />
          </dl>
        </div>
      </Panel>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" onClick={onBack}>
          {t("backToEdit")}
        </Button>
        <Button
          type="button"
          variant="default"
          disabled={submitting}
          onClick={onSubmit}
        >
          {submitting ? t("submitting") : t("submit")}
        </Button>
      </div>
    </div>
  )
}
