import type {
  AttendeeProfile,
  ExecutionMode,
  TicketPreference,
  TicketRule,
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
  auto_cloudflare: boolean
  auto_ocr: boolean
  auto_submit_verification: boolean
  ocr_model_path: string
  ocr_max_retries: number
  cloudflare_max_retries: number
  debug_screenshots_and_logs: boolean
  execution_mode: ExecutionMode
  profile: string
}

/**
 * 預設就排除優待票與身障票。
 *
 * 這兩種票入場要查驗證件，資格不符當場作廢——誤買的代價不對稱，所以預設站在
 * 「不要買到」那一邊；真的要買的人在欄位裡把關鍵字刪掉即可。
 */
export function createDefaultRule(): TicketRule {
  return {
    max_price: null,
    min_price: null,
    prefer_name_patterns: [],
    exclude_name_patterns: ["優待", "身障", "愛心", "敬老"],
    price_order: "page",
  }
}

export function createInitialDraft(): TaskDraft {
  return {
    ticket_preference: {
      quantity: 2,
      // 開賣前多半拿不到票價（拓元、ibon 的票價要進到票區頁才看得到），所以預設
      // 不放任何精確價格。留一筆 price 0 的佔位更糟：後端會拿它去比對「頁面沒印
      // 價格」的票種並搶在規則前面成立，等於規則整組失效。
      priorities: [],
      seat_preference: {
        adjacent: true,
        strategy: "best_available",
        preferred_zones: [],
      },
      fallback_to_any: false,
      rule: createDefaultRule(),
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
    auto_cloudflare: true,
    auto_ocr: true,
    auto_submit_verification: true,
    ocr_model_path: "",
    ocr_max_retries: 5,
    cloudflare_max_retries: 3,
    debug_screenshots_and_logs: false,
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
    case "ocrMaxRetries":
      return "ocr-max-retries"
    case "cloudflareMaxRetries":
      return "cloudflare-max-retries"
    case "executionMode":
      return "execution-mode"
    case "priorities":
      return "priority-order-0"
    case "rule":
      return "rule-max-price"
    default:
      return key
  }
}

function validateRule(
  rule: TicketRule | null,
  message: (key: string, values?: Record<string, string | number>) => string
): string | null {
  if (rule === null) return null
  const prices = [rule.min_price, rule.max_price]
  if (prices.some((p) => p !== null && (!Number.isFinite(p) || p < 0))) {
    return message("rulePriceNegative")
  }
  // 後端 TicketRule 有同一條 model_validator；前端先擋才不會換來一個 422。
  if (
    rule.min_price !== null &&
    rule.max_price !== null &&
    rule.min_price > rule.max_price
  ) {
    return message("rulePriceWindowInverted")
  }
  return null
}

export interface ValidateOptions {
  /** 每次驗證都由呼叫端給「當下」的時間——送出前必須重新取得，不能沿用進頁時的值。 */
  now: number
  accountConfigured: boolean
  /** 活動已經在販售時為 false：沒有開賣可等，表單上也沒有搶票時間這個欄位。 */
  needsTicketingTime: boolean
  message: (key: string, values?: Record<string, string | number>) => string
}

/**
 * 表單驗證。回傳「欄位鍵 → 訊息」，空物件代表通過。
 *
 * 純函式：可以用固定時間直接測，不必渲染整張表單。
 */
export function validateDraft(
  draft: TaskDraft,
  { now, accountConfigured, needsTicketingTime, message }: ValidateOptions
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

  // 精確票種、規則、退而求其次——三種挑票方式至少要有一種，否則送出去的是一個
  // 保證買不到票的任務（後端 TicketPreference 也擋，這裡先擋是為了指到欄位）。
  if (
    tp.priorities.length === 0 &&
    tp.rule === null &&
    !tp.fallback_to_any
  ) {
    errors.priorities = message("noWayToPick")
  } else if (tp.priorities.some((p) => !Number.isFinite(p.price) || p.price < 0)) {
    errors.priorities = message("priorityPriceNegative")
  }

  const ruleError = validateRule(tp.rule, message)
  if (ruleError !== null) {
    errors.rule = ruleError
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

  if (needsTicketingTime) {
    if (draft.ticketing_time_local === "") {
      errors.ticketingTime = message("ticketingTimeRequired")
    } else if (!isTicketingTimeParsable(draft.ticketing_time_local)) {
      errors.ticketingTime = message("ticketingTimeInvalid")
    } else if (isTicketingTimeInPast(draft.ticketing_time_local, now)) {
      errors.ticketingTime = message("ticketingTimePast")
    }
  }

  if (!Number.isFinite(draft.timeout_seconds) || draft.timeout_seconds <= 0) {
    errors.timeoutSeconds = message("timeoutPositive")
  }
  if (!Number.isFinite(draft.max_retries) || draft.max_retries < 0) {
    errors.maxRetries = message("maxRetriesNegative")
  }

  if (
    !Number.isFinite(draft.ocr_max_retries) ||
    draft.ocr_max_retries < 1 ||
    draft.ocr_max_retries > 20 ||
    !Number.isInteger(draft.ocr_max_retries)
  ) {
    errors.ocrMaxRetries = message("ocrMaxRetriesRange")
  }
  if (
    !Number.isFinite(draft.cloudflare_max_retries) ||
    draft.cloudflare_max_retries < 0 ||
    draft.cloudflare_max_retries > 20 ||
    !Number.isInteger(draft.cloudflare_max_retries)
  ) {
    errors.cloudflareMaxRetries = message("cloudflareMaxRetriesRange")
  }

  // 正式模式沒有帳號設定時，連請求都不要送——後端也會擋，但使用者該在這裡就知道。
  if (draft.execution_mode === "live" && !accountConfigured) {
    errors.executionMode = message("accountNotConfigured")
  }

  return errors
}
