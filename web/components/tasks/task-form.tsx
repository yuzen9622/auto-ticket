"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { EventSummary } from "@/components/tasks/event-summary"
import { AttendeeEditor } from "@/components/tasks/attendee-editor"
import { ContactPicker } from "@/components/tasks/contact-picker"
import { TaskConfirm } from "@/components/tasks/task-confirm"
import { TicketPriorityEditor } from "@/components/tasks/ticket-priority-editor"
import { VerificationRuleEditor } from "@/components/tasks/verification-rule-editor"
import { EmptyState } from "@/components/terminal/empty-state"
import { Panel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { getAccountStatus } from "@/lib/api/accounts"
import { ApiError } from "@/lib/api/client"
import { getEvent } from "@/lib/api/events"
import { createTask } from "@/lib/api/tasks"
import type { CreateTaskRequest, EventOut } from "@/lib/api/types"
import { EXECUTION_MODE, LIVE_PLATFORM } from "@/lib/execution-mode"
import { formatDateTime, toOffsetIso } from "@/lib/format"
import { useApiErrorMessage, isAccountNotConfigured } from "@/lib/i18n/errors"
import { useExecutionModeLabel, useSeatStrategyLabel } from "@/lib/i18n/labels"
import { SEAT_STRATEGY } from "@/lib/contract"
import {
  createInitialDraft,
  FIELD_ELEMENT_ID,
  validateDraft,
  type TaskDraft,
} from "@/lib/task-draft"
import {
  defaultTicketingTimeLocal,
  floorToMinute,
  minTicketingTimeLocal,
} from "@/lib/ticketing-time"

/** 目前這一分鐘的起點；每 15 秒對一次時鐘，跨分鐘時才會重新繪製。 */
function useCurrentMinute(): number {
  const subscribe = React.useCallback((onChange: () => void) => {
    const timer = setInterval(onChange, 15_000)
    return () => clearInterval(timer)
  }, [])

  return React.useSyncExternalStore(
    subscribe,
    () => floorToMinute(Date.now()),
    () => 0
  )
}

export function TaskForm({ eventId }: { eventId: string }) {
  const t = useTranslations("taskForm")
  const tEvent = useTranslations("event")
  const tErrors = useTranslations("taskFormErrors")
  const tList = useTranslations("taskList")
  const common = useTranslations("common")
  const router = useRouter()
  const apiErrorMessage = useApiErrorMessage()
  const seatStrategyLabel = useSeatStrategyLabel()
  const executionModeLabel = useExecutionModeLabel()

  // 活動一律以 eventId 重新取得；不依賴首頁傳下來的 state，重新整理也能運作。
  const eventQuery = useQuery({
    queryKey: ["event", eventId],
    queryFn: ({ signal }) => getEvent(eventId, { signal }),
    retry: false,
  })
  const event: EventOut | undefined = eventQuery.data

  const accountQuery = useQuery({
    queryKey: ["account-status", LIVE_PLATFORM],
    queryFn: () => getAccountStatus(LIVE_PLATFORM),
  })
  const accountConfigured = accountQuery.data?.configured ?? false

  const [draft, setDraft] = React.useState<TaskDraft>(createInitialDraft)
  // datetime-local 的 min 要跟著時鐘走，但 min 只是第一道門檻——
  // 真正擋下過期時間的是送出前的重新驗證。
  const currentMinute = useCurrentMinute()
  const [errors, setErrors] = React.useState<Record<string, string>>({})
  const [confirming, setConfirming] = React.useState(false)

  // 搶票時間只在「第一次拿到這場活動」時帶入；同一場活動的重新抓取不得蓋掉使用者輸入。
  const seededEventId = React.useRef<string | null>(null)
  const timeTouched = React.useRef(false)
  React.useEffect(() => {
    if (!event || seededEventId.current === event.id) return
    seededEventId.current = event.id
    timeTouched.current = false
    setDraft((prev) => ({
      ...prev,
      ticketing_time_local: defaultTicketingTimeLocal(
        event.sale_start_at,
        Date.now()
      ),
      // 票價是完全相等比對，預設值 0 會把所有票排除掉。活動票種已經抓回來了，
      // 就用它預填，讓表單一打開就是「照票面價買」而不是一個買不到任何票的設定。
      ticket_preference: event.ticket_types.length
        ? {
            ...prev.ticket_preference,
            priorities: event.ticket_types.map((ticket, index) => ({
              price: ticket.price,
              ticket_name_pattern: ticket.name,
              priority: index + 1,
            })),
          }
        : prev.ticket_preference,
    }))
  }, [event])

  const patch = (next: Partial<TaskDraft>) =>
    setDraft((prev) => ({ ...prev, ...next }))

  const create = useMutation({
    mutationFn: (body: CreateTaskRequest) => createTask(body),
    onSuccess: (task) => {
      toast.success(tList("created"))
      router.push(`/tasks/${task.id}`)
    },
    onError: (error) => {
      setConfirming(false)
      if (isAccountNotConfigured(error)) {
        setErrors({ executionMode: tErrors("accountNotConfigured") })
        return
      }
      toast.error(
        error instanceof ApiError
          ? apiErrorMessage(error)
          : tList("actionFailed")
      )
    },
  })

  const focusFirstError = (found: Record<string, string>) => {
    const [firstKey] = Object.keys(found)
    if (!firstKey) return
    const elementId = FIELD_ELEMENT_ID(firstKey)
    requestAnimationFrame(() => {
      document.getElementById(elementId)?.focus()
    })
  }

  /** 每次都用「當下」的時間重驗：停在頁面期間過期的搶票時間必須被擋下來。 */
  const runValidation = () => {
    const found = validateDraft(draft, {
      now: Date.now(),
      accountConfigured,
      message: (key, values) => tErrors(key, values),
    })
    setErrors(found)
    return found
  }

  const onReview = () => {
    const found = runValidation()
    if (Object.keys(found).length > 0) {
      setConfirming(false)
      focusFirstError(found)
      return
    }
    setConfirming(true)
  }

  const onSubmit = () => {
    if (!event) return
    const found = runValidation()
    if (Object.keys(found).length > 0) {
      setConfirming(false)
      focusFirstError(found)
      return
    }
    create.mutate({
      event_title: event.title,
      event_url: event.canonical_url,
      // 一律送含時區位移的 ISO-8601，後端才不會把本地時間當成 UTC。
      sale_start_at: toOffsetIso(draft.ticketing_time_local),
      ticket_preference: draft.ticket_preference,
      contact_profile: draft.contact_profile,
      attendees: draft.attendees,
      execution_mode: draft.execution_mode,
      max_retries: draft.max_retries,
      timeout_seconds: draft.timeout_seconds,
      verification_rules: draft.verification_rules,
      auto_login: draft.auto_login,
      qualification_code: draft.qualification_code.trim() || null,
      session_preference: draft.session_preference.trim() || null,
      profile: draft.profile,
    })
  }

  if (eventQuery.isPending) {
    return <EmptyState message={tEvent("loading")} />
  }

  if (eventQuery.isError || !event) {
    const notFound =
      eventQuery.error instanceof ApiError && eventQuery.error.status === 404
    return (
      <EmptyState
        message={notFound ? tEvent("notFound") : tEvent("loadFailed")}
        hint={notFound ? tEvent("notFoundHint") : tEvent("loadFailedHint")}
        action={
          <Button asChild size="sm" variant="outline">
            <Link href="/">{common("backToSearch")}</Link>
          </Button>
        }
      />
    )
  }

  const tp = draft.ticket_preference
  const contact = draft.contact_profile
  const ticketNames = event.ticket_types.map((ticket) => ticket.name)
  const errorCount = Object.keys(errors).length

  if (confirming) {
    return (
      <TaskConfirm
        event={event}
        draft={draft}
        submitting={create.isPending}
        onBack={() => setConfirming(false)}
        onSubmit={onSubmit}
      />
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <EventSummary event={event} />

      {errorCount > 0 && (
        <div
          role="alert"
          aria-live="assertive"
          className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          {tErrors("heading", { count: errorCount })}
        </div>
      )}

      <Panel title={t("ticketSection")}>
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="quantity">{t("quantity")}</Label>
              <Input
                id="quantity"
                type="number"
                min={1}
                max={10}
                value={tp.quantity}
                onChange={(e) =>
                  patch({
                    ticket_preference: {
                      ...tp,
                      quantity: Number(e.target.value),
                    },
                  })
                }
                aria-invalid={errors.quantity !== undefined}
                aria-describedby="quantity-hint"
                className="tabular h-7"
              />
              <span
                id="quantity-hint"
                className="text-xs text-muted-foreground"
              >
                {errors.quantity ?? t("quantityHint")}
              </span>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="fallback-to-any">{t("fallbackToAny")}</Label>
              <Switch
                id="fallback-to-any"
                checked={tp.fallback_to_any}
                onCheckedChange={(v) =>
                  patch({ ticket_preference: { ...tp, fallback_to_any: v } })
                }
              />
            </div>
          </div>

          <TicketPriorityEditor
            value={tp.priorities}
            ticketNames={ticketNames}
            onChange={(priorities) =>
              patch({ ticket_preference: { ...tp, priorities } })
            }
          />
        </div>
      </Panel>

      <Panel title={t("seatSection")}>
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="seat-strategy">{t("seatStrategy")}</Label>
              <Select
                value={tp.seat_preference.strategy}
                onValueChange={(v) =>
                  patch({
                    ticket_preference: {
                      ...tp,
                      seat_preference: {
                        ...tp.seat_preference,
                        strategy:
                          v as TaskDraft["ticket_preference"]["seat_preference"]["strategy"],
                      },
                    },
                  })
                }
              >
                <SelectTrigger id="seat-strategy" size="sm" className="h-7">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEAT_STRATEGY.map((strategy) => (
                    <SelectItem key={strategy} value={strategy}>
                      {seatStrategyLabel(strategy)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="adjacent">{t("adjacent")}</Label>
              <Switch
                id="adjacent"
                checked={tp.seat_preference.adjacent}
                onCheckedChange={(v) =>
                  patch({
                    ticket_preference: {
                      ...tp,
                      seat_preference: { ...tp.seat_preference, adjacent: v },
                    },
                  })
                }
              />
            </div>
          </div>

          {tp.seat_preference.strategy === "specific_zone" && (
            <div className="flex flex-col gap-1">
              <Label htmlFor="preferred-zones">{t("preferredZones")}</Label>
              <Input
                id="preferred-zones"
                value={tp.seat_preference.preferred_zones.join(", ")}
                onChange={(e) =>
                  patch({
                    ticket_preference: {
                      ...tp,
                      seat_preference: {
                        ...tp.seat_preference,
                        preferred_zones: e.target.value
                          .split(",")
                          .map((zone) => zone.trim())
                          .filter(Boolean),
                      },
                    },
                  })
                }
                aria-invalid={errors.preferredZones !== undefined}
                aria-describedby="preferred-zones-hint"
                className="h-7"
              />
              <span
                id="preferred-zones-hint"
                className={
                  errors.preferredZones
                    ? "text-xs text-destructive"
                    : "text-xs text-muted-foreground"
                }
              >
                {errors.preferredZones ?? t("preferredZonesHint")}
              </span>
            </div>
          )}
        </div>
      </Panel>

      <Panel title={t("contactSection")}>
        <div className="flex flex-col gap-3">
          <ContactPicker
            current={contact}
            onPick={(picked) => patch({ contact_profile: picked })}
          />

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {(
              [
                ["contactName", "contact-name", "name"],
                ["contactPhone", "contact-phone", "phone"],
                ["contactEmail", "contact-email", "email"],
              ] as const
            ).map(([labelKey, elementId, field]) => {
              const message = errors[elementId]
              return (
                <div key={elementId} className="flex flex-col gap-1">
                  <Label htmlFor={elementId}>{t(labelKey)}</Label>
                  <Input
                    id={elementId}
                    value={contact[field]}
                    onChange={(e) =>
                      patch({
                        contact_profile: {
                          ...contact,
                          [field]: e.target.value,
                        },
                      })
                    }
                    aria-invalid={message !== undefined}
                    aria-describedby={
                      message ? `${elementId}-error` : undefined
                    }
                    className="h-7"
                    autoComplete="off"
                  />
                  {message && (
                    <span
                      id={`${elementId}-error`}
                      className="text-xs text-destructive"
                    >
                      {message}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </Panel>

      <Panel title={t("attendeeSection")}>
        <AttendeeEditor
          value={draft.attendees}
          errors={errors}
          onChange={(attendees) => patch({ attendees })}
        />
      </Panel>

      <Panel title={t("verificationSection")}>
        <div className="flex flex-col gap-3">
          <VerificationRuleEditor
            value={draft.verification_rules}
            errors={errors}
            onChange={(verification_rules) => patch({ verification_rules })}
          />
          <div className="flex flex-col gap-1">
            <Label htmlFor="session-preference">{t("sessionPreference")}</Label>
            <Input
              id="session-preference"
              value={draft.session_preference}
              onChange={(e) => patch({ session_preference: e.target.value })}
              aria-describedby="session-preference-hint"
              className="h-7"
              autoComplete="off"
            />
            <span
              id="session-preference-hint"
              className="text-xs text-muted-foreground"
            >
              {t("sessionPreferenceHint")}
            </span>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="qualification-code">{t("qualificationCode")}</Label>
            <Input
              id="qualification-code"
              value={draft.qualification_code}
              onChange={(e) => patch({ qualification_code: e.target.value })}
              aria-describedby="qualification-code-hint"
              className="h-7"
              autoComplete="off"
            />
            <span
              id="qualification-code-hint"
              className="text-xs text-muted-foreground"
            >
              {t("qualificationCodeHint")}
            </span>
          </div>
        </div>
      </Panel>

      <Panel title={t("executionSection")}>
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="ticketing-time">{t("ticketingTime")}</Label>
              <Input
                id="ticketing-time"
                type="datetime-local"
                value={draft.ticketing_time_local}
                min={minTicketingTimeLocal(currentMinute)}
                onChange={(e) => {
                  timeTouched.current = true
                  patch({ ticketing_time_local: e.target.value })
                }}
                aria-invalid={errors.ticketingTime !== undefined}
                aria-describedby="ticketing-time-hint"
                className="tabular h-7"
              />
              <span
                id="ticketing-time-hint"
                className={
                  errors.ticketingTime
                    ? "text-xs text-destructive"
                    : "text-xs text-muted-foreground"
                }
              >
                {errors.ticketingTime ??
                  (event.sale_start_at
                    ? t("ticketingTimeHint")
                    : t("saleStartMissing"))}
              </span>
              <span className="tabular text-xs text-muted-foreground">
                {t("originalSaleStart")}：
                {event.sale_start_at
                  ? formatDateTime(event.sale_start_at)
                  : common("none")}
              </span>
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="execution-mode">{t("executionMode")}</Label>
              <Select
                value={draft.execution_mode}
                onValueChange={(v) =>
                  patch({ execution_mode: v as TaskDraft["execution_mode"] })
                }
              >
                <SelectTrigger id="execution-mode" size="sm" className="h-7">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXECUTION_MODE.map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {executionModeLabel(mode)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span
                id="execution-mode-hint"
                className={
                  errors.executionMode
                    ? "text-xs text-destructive"
                    : "text-xs text-muted-foreground"
                }
              >
                {errors.executionMode ??
                  (draft.execution_mode === "live"
                    ? t("liveModeNotice")
                    : t("mockModeNotice"))}
              </span>
              {draft.execution_mode === "live" && !accountConfigured && (
                <Button asChild size="sm" variant="outline" className="w-fit">
                  <Link href="/settings">{t("goToSettings")}</Link>
                </Button>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="max-retries">{t("maxRetries")}</Label>
              <Input
                id="max-retries"
                type="number"
                min={0}
                value={draft.max_retries}
                onChange={(e) => patch({ max_retries: Number(e.target.value) })}
                aria-invalid={errors.maxRetries !== undefined}
                aria-describedby={
                  errors.maxRetries ? "max-retries-error" : undefined
                }
                className="tabular h-7"
              />
              {errors.maxRetries && (
                <span
                  id="max-retries-error"
                  className="text-xs text-destructive"
                >
                  {errors.maxRetries}
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="timeout-seconds">{t("timeoutSeconds")}</Label>
              <Input
                id="timeout-seconds"
                type="number"
                min={1}
                value={draft.timeout_seconds}
                onChange={(e) =>
                  patch({ timeout_seconds: Number(e.target.value) })
                }
                aria-invalid={errors.timeoutSeconds !== undefined}
                aria-describedby={
                  errors.timeoutSeconds ? "timeout-seconds-error" : undefined
                }
                className="tabular h-7"
              />
              {errors.timeoutSeconds && (
                <span
                  id="timeout-seconds-error"
                  className="text-xs text-destructive"
                >
                  {errors.timeoutSeconds}
                </span>
              )}
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="auto-login">{t("autoLogin")}</Label>
              <Switch
                id="auto-login"
                checked={draft.auto_login}
                onCheckedChange={(v) => patch({ auto_login: v })}
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="browser-profile">{t("browserProfile")}</Label>
              <Input
                id="browser-profile"
                value={draft.profile}
                onChange={(e) => patch({ profile: e.target.value })}
                className="h-7"
              />
            </div>
          </div>
        </div>
      </Panel>

      <div className="flex justify-end">
        <Button type="button" variant="default" onClick={onReview}>
          {t("continue")}
        </Button>
      </div>
    </div>
  )
}
