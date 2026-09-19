import type {
  AttendeeProfile,
  ExecutionMode,
  TicketPreference,
  UserContactProfile,
  VerificationRule,
} from "@/lib/api/types"
import { EMAIL_PATTERN, PHONE_PATTERN } from "@/lib/contract"
import { isTicketingTimeInPast, isTicketingTimeParsable } from "@/lib/ticketing-time"

export interface TaskDraft {
  ticket_preference: TicketPreference
  contact_profile: UserContactProfile
  attendees: AttendeeProfile[]
  verification_rules: VerificationRule[]
  /** 本地時間字串（`datetime-local` 的格式），送出前才轉成含時區位移的 ISO-8601。 */
  ticketing_time_local: string
  max_retries: number
  timeout_seconds: number
  auto_login: boolean
  qualification_code: string
  session_preference: string
  execution_mode: ExecutionMode
  profile: string
}

export function createInitialDraft(): TaskDraft {
  return {
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
    ticketing_time_local: "",
    max_retries: 3,
    timeout_seconds: 120,
    auto_login: false,
    qualification_code: "",
    session_preference: "",
    // 產品預設是正式模式；測試模式必須由使用者自己選。
    execution_mode: "live",
    profile: "live",
  }
}

/** 錯誤鍵 → 要被聚焦的元素 id。錯誤訊息一定顯示在該欄位旁邊。 */
export function FIELD_ELEMENT_ID(key: string): string {
  const attendee = /^attendee\.(\d+)\.(name|phone)$/.exec(key)
  if (attendee) return `attendee-${attendee[2]}-${attendee[1]}`
  const rule = /^verification\.(\d+)\.(pattern|answer)$/.exec(key)
  if (rule) return `rule-${rule[2]}-${rule[1]}`
  switch (key) {
    case "preferredZones":
      return "preferred-zones"
    case "ticketingTime":
      return "ticketing-time"
    case "timeoutSeconds":
      return "timeout-seconds"
    case "maxRetries":
      return "max-retries"
    case "executionMode":
      return "execution-mode"
    case "priorities":
      return "priority-order-0"
    default:
      return key
  }
}

export interface ValidateOptions {
  /** 每次驗證都由呼叫端給「當下」的時間——送出前必須重新取得，不能沿用進頁時的值。 */
  now: number
  accountConfigured: boolean
  message: (key: string, values?: Record<string, string | number>) => string
}

/**
 * 表單驗證。回傳「欄位鍵 → 訊息」，空物件代表通過。
 *
 * 純函式：可以用固定時間直接測，不必渲染整張表單。
 */
export function validateDraft(
  draft: TaskDraft,
  { now, accountConfigured, message }: ValidateOptions
): Record<string, string> {
  const errors: Record<string, string> = {}
  const tp = draft.ticket_preference

  if (
    !Number.isFinite(tp.quantity) ||
    tp.quantity < 1 ||
    tp.quantity > 10 ||
    !Number.isInteger(tp.quantity)
  ) {
    errors.quantity = message("quantityRange")
  }

  if (tp.priorities.length === 0) {
    errors.priorities = message("prioritiesEmpty")
  } else if (tp.priorities.some((p) => !Number.isFinite(p.price) || p.price < 0)) {
    errors.priorities = message("priorityPriceNegative")
  }

  if (
    tp.seat_preference.strategy === "specific_zone" &&
    tp.seat_preference.preferred_zones.length === 0
  ) {
    errors.preferredZones = message("zonesRequired")
  }

  if (draft.contact_profile.name.trim() === "") {
    errors["contact-name"] = message("contactNameRequired")
  }
  if (!PHONE_PATTERN.test(draft.contact_profile.phone)) {
    errors["contact-phone"] = message("contactPhoneInvalid")
  }
  if (!EMAIL_PATTERN.test(draft.contact_profile.email)) {
    errors["contact-email"] = message("contactEmailInvalid")
  }

  draft.attendees.forEach((attendee, index) => {
    if (attendee.name.trim() === "") {
      errors[`attendee.${index}.name`] = message("attendeeNameRequired", {
        index: index + 1,
      })
    }
    if (!PHONE_PATTERN.test(attendee.phone)) {
      errors[`attendee.${index}.phone`] = message("attendeePhoneInvalid", {
        index: index + 1,
      })
    }
  })

  draft.verification_rules.forEach((rule, index) => {
    if (rule.pattern.trim() === "") {
      errors[`verification.${index}.pattern`] = message(
        "verificationPatternRequired",
        { index: index + 1 }
      )
    }
    if (rule.answer.trim() === "") {
      errors[`verification.${index}.answer`] = message(
        "verificationAnswerRequired",
        { index: index + 1 }
      )
    }
  })

  if (draft.ticketing_time_local === "") {
    errors.ticketingTime = message("ticketingTimeRequired")
  } else if (!isTicketingTimeParsable(draft.ticketing_time_local)) {
    errors.ticketingTime = message("ticketingTimeInvalid")
  } else if (isTicketingTimeInPast(draft.ticketing_time_local, now)) {
    errors.ticketingTime = message("ticketingTimePast")
  }

  if (!Number.isFinite(draft.timeout_seconds) || draft.timeout_seconds <= 0) {
    errors.timeoutSeconds = message("timeoutPositive")
  }
  if (!Number.isFinite(draft.max_retries) || draft.max_retries < 0) {
    errors.maxRetries = message("maxRetriesNegative")
  }

  // 正式模式沒有帳號設定時，連請求都不要送——後端也會擋，但使用者該在這裡就知道。
  if (draft.execution_mode === "live" && !accountConfigured) {
    errors.executionMode = message("accountNotConfigured")
  }

  return errors
}
