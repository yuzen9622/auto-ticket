"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { useMutation } from "@tanstack/react-query"
import { toast } from "sonner"

import { StepConfirm, type RunOptions } from "@/components/tasks/step-confirm"
import { StepEvent } from "@/components/tasks/step-event"
import {
  StepPreference,
  type PreferenceDraft,
} from "@/components/tasks/step-preference"
import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/api/client"
import { createTask } from "@/lib/api/tasks"
import type { CreateTaskRequest, EventOut } from "@/lib/api/types"
import { EMAIL_PATTERN, PHONE_PATTERN } from "@/lib/contract"
import { toLocalInputValue, toOffsetIso } from "@/lib/format"

const STEPS = ["活動解析", "票務偏好", "確認送出"] as const

const INITIAL_DRAFT: PreferenceDraft = {
  ticket_preference: {
    quantity: 2,
    priorities: [{ price: 0, ticket_name_pattern: null, priority: 1 }],
    seat_preference: {
      adjacent: true,
      strategy: "best_available",
      preferred_zones: [],
    },
    fallback_to_any: false,
  },
  contact_profile: { name: "", phone: "", email: "" },
  attendees: [],
  verification_rules: [],
}

const INITIAL_OPTIONS: RunOptions = {
  sale_start_at_local: "",
  auto_login: false,
  max_retries: 3,
  timeout_seconds: 120,
  qualification_code: "",
  profile: "live",
}

export function TaskWizard() {
  const router = useRouter()
  const [step, setStep] = React.useState(0)
  const [event, setEvent] = React.useState<EventOut | null>(null)
  const [draft, setDraft] = React.useState<PreferenceDraft>(INITIAL_DRAFT)
  const [options, setOptions] = React.useState<RunOptions>(INITIAL_OPTIONS)

  const onResolved = (ev: EventOut) => {
    setEvent(ev)
    // 解析出的開賣時間直接帶入，使用者仍可覆寫。
    setOptions((o) => ({
      ...o,
      sale_start_at_local:
        toLocalInputValue(ev.sale_start_at) || o.sale_start_at_local,
    }))
  }

  const contactValid =
    draft.contact_profile.name.trim() !== "" &&
    PHONE_PATTERN.test(draft.contact_profile.phone) &&
    EMAIL_PATTERN.test(draft.contact_profile.email)

  const attendeesValid = draft.attendees.every(
    (a) => a.name.trim() !== "" && PHONE_PATTERN.test(a.phone)
  )

  const rulesValid = draft.verification_rules.every(
    (r) => r.pattern.trim() !== "" && r.answer.trim() !== ""
  )

  const saleValid =
    options.sale_start_at_local !== "" &&
    !Number.isNaN(new Date(options.sale_start_at_local).getTime())

  const canNext =
    step === 0
      ? event !== null
      : step === 1
        ? contactValid && attendeesValid && rulesValid
        : false

  const create = useMutation({
    mutationFn: (body: CreateTaskRequest) => createTask(body),
    onSuccess: (task) => {
      toast.success("任務已建立")
      router.push(`/tasks/${task.id}`)
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : "建立任務失敗")
    },
  })

  const submit = () => {
    if (!event || !saleValid) return
    create.mutate({
      event_title: event.title,
      event_url: event.canonical_url,
      // 一律送含時區位移的 ISO-8601（R13）。
      sale_start_at: toOffsetIso(options.sale_start_at_local),
      ticket_preference: draft.ticket_preference,
      contact_profile: draft.contact_profile,
      attendees: draft.attendees,
      payment_method: "mock",
      max_retries: options.max_retries,
      timeout_seconds: options.timeout_seconds,
      verification_rules: draft.verification_rules,
      auto_login: options.auto_login,
      qualification_code: options.qualification_code.trim() || null,
      profile: options.profile,
    })
  }

  return (
    <div className="flex flex-col gap-4">
      <ol className="flex items-center gap-2 text-[11px]">
        {STEPS.map((label, i) => (
          <li key={label} className="flex items-center gap-2">
            <span
              className={
                i === step
                  ? "rounded-[4px] border border-[var(--oc-accent)] px-2 py-0.5 text-[var(--oc-accent)]"
                  : i < step
                    ? "rounded-[4px] border border-[var(--oc-border)] px-2 py-0.5 text-[var(--oc-success)]"
                    : "rounded-[4px] border border-[var(--oc-border)] px-2 py-0.5 text-[var(--oc-muted)]"
              }
            >
              {i + 1}. {label}
            </span>
            {i < STEPS.length - 1 && (
              <span className="text-[var(--oc-muted)]">──</span>
            )}
          </li>
        ))}
      </ol>

      {step === 0 && <StepEvent event={event} onResolved={onResolved} />}
      {step === 1 && (
        <StepPreference event={event} draft={draft} onChange={setDraft} />
      )}
      {step === 2 && (
        <StepConfirm
          event={event}
          draft={draft}
          options={options}
          onChange={setOptions}
        />
      )}

      <div className="flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          上一步
        </Button>

        {step < 2 ? (
          <Button
            type="button"
            variant="accent"
            size="sm"
            disabled={!canNext}
            onClick={() => setStep((s) => s + 1)}
          >
            下一步
          </Button>
        ) : (
          <Button
            type="button"
            variant="accent"
            size="sm"
            disabled={!saleValid || create.isPending}
            onClick={submit}
          >
            {create.isPending ? "建立中…" : "建立任務（Mock 付款）"}
          </Button>
        )}
      </div>
    </div>
  )
}
