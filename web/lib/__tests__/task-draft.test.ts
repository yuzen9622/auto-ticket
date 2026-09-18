import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { createInitialDraft, FIELD_ELEMENT_ID, validateDraft } from "@/lib/task-draft"
import { defaultTicketingTimeLocal, msToLocalInput } from "@/lib/ticketing-time"

const NOW_ISO = "2026-09-18T10:30:45.500Z"

/** 驗證訊息的內容由字典負責，這裡只要知道「哪個欄位錯了」。 */
const message = (key: string) => key

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(NOW_ISO))
})

afterEach(() => {
  vi.useRealTimers()
})

function validDraft() {
  return {
    ...createInitialDraft(),
    contact_profile: {
      name: "王小明",
      phone: "0912345678",
      email: "user@example.test",
    },
    ticketing_time_local: defaultTicketingTimeLocal(
      "2026-10-01T12:00:00Z",
      Date.now()
    ),
  }
}

function run(draft: ReturnType<typeof validDraft>, accountConfigured = true) {
  return validateDraft(draft, { now: Date.now(), accountConfigured, message })
}

describe("validateDraft", () => {
  it("預設是正式模式", () => {
    expect(createInitialDraft().execution_mode).toBe("live")
  })

  it("完整資料通過驗證", () => {
    expect(run(validDraft())).toEqual({})
  })

  it("張數超出範圍會被擋下", () => {
    const draft = validDraft()
    draft.ticket_preference.quantity = 11
    expect(run(draft).quantity).toBe("quantityRange")
  })

  it("聯絡人姓名不可空白", () => {
    const draft = validDraft()
    draft.contact_profile.name = "  "
    expect(run(draft)["contact-name"]).toBe("contactNameRequired")
  })

  it("電話與 Email 格式錯誤各自標記", () => {
    const draft = validDraft()
    draft.contact_profile.phone = "123"
    draft.contact_profile.email = "not-an-email"
    const errors = run(draft)
    expect(errors["contact-phone"]).toBe("contactPhoneInvalid")
    expect(errors["contact-email"]).toBe("contactEmailInvalid")
  })

  it("參加者資料不完整時指出是第幾位", () => {
    const draft = validDraft()
    draft.attendees = [{ name: "", phone: "bad", id_number: null }]
    const errors = run(draft)
    expect(errors["attendee.0.name"]).toBe("attendeeNameRequired")
    expect(errors["attendee.0.phone"]).toBe("attendeePhoneInvalid")
  })

  it("指定區域策略下至少要有一個區域", () => {
    const draft = validDraft()
    draft.ticket_preference.seat_preference.strategy = "specific_zone"
    expect(run(draft).preferredZones).toBe("zonesRequired")

    draft.ticket_preference.seat_preference.preferred_zones = ["A 區"]
    expect(run(draft).preferredZones).toBeUndefined()
  })

  it("票種優先順序不可為空", () => {
    const draft = validDraft()
    draft.ticket_preference.priorities = []
    expect(run(draft).priorities).toBe("prioritiesEmpty")
  })

  it("驗證規則缺題目或答案時逐條標記", () => {
    const draft = validDraft()
    draft.verification_rules = [{ pattern: "", answer: "", is_regex: false }]
    const errors = run(draft)
    expect(errors["verification.0.pattern"]).toBe("verificationPatternRequired")
    expect(errors["verification.0.answer"]).toBe("verificationAnswerRequired")
  })

  it("搶票時間早於現在會被擋下", () => {
    const draft = validDraft()
    draft.ticketing_time_local = msToLocalInput(Date.now() - 3_600_000)
    expect(run(draft).ticketingTime).toBe("ticketingTimePast")
  })

  it("剛帶入的目前時間不算過期，但停留超過一分鐘後再驗就過期", () => {
    const draft = validDraft()
    draft.ticketing_time_local = defaultTicketingTimeLocal(null, Date.now())
    expect(run(draft).ticketingTime).toBeUndefined()

    vi.advanceTimersByTime(61_000)
    expect(run(draft).ticketingTime).toBe("ticketingTimePast")
  })

  it("逾時時間必須大於零、重試次數不得為負", () => {
    const draft = validDraft()
    draft.timeout_seconds = 0
    draft.max_retries = -1
    const errors = run(draft)
    expect(errors.timeoutSeconds).toBe("timeoutPositive")
    expect(errors.maxRetries).toBe("maxRetriesNegative")
  })

  it("正式模式缺少帳號設定時擋下送出", () => {
    expect(run(validDraft(), false).executionMode).toBe("accountNotConfigured")
  })

  it("測試模式不需要帳號設定", () => {
    const draft = validDraft()
    draft.execution_mode = "mock"
    expect(run(draft, false)).toEqual({})
  })
})

describe("FIELD_ELEMENT_ID", () => {
  it("錯誤鍵對應得到可聚焦的欄位 id", () => {
    expect(FIELD_ELEMENT_ID("contact-name")).toBe("contact-name")
    expect(FIELD_ELEMENT_ID("attendee.2.phone")).toBe("attendee-phone-2")
    expect(FIELD_ELEMENT_ID("verification.1.answer")).toBe("rule-answer-1")
    expect(FIELD_ELEMENT_ID("ticketingTime")).toBe("ticketing-time")
    expect(FIELD_ELEMENT_ID("preferredZones")).toBe("preferred-zones")
  })
})
