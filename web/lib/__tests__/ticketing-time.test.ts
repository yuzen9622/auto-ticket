import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  defaultTicketingTimeLocal,
  defaultTicketingTimeMs,
  floorToMinute,
  isTicketingTimeInPast,
  minTicketingTimeLocal,
  msToLocalInput,
} from "@/lib/ticketing-time"

/** 時間測試一律固定系統時間，否則秒針一走就變成間歇失敗。 */
const NOW_ISO = "2026-09-18T10:30:45.500Z"

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(NOW_ISO))
})

afterEach(() => {
  vi.useRealTimers()
})

describe("defaultTicketingTimeMs", () => {
  it("開賣時間還沒到時，預設帶入活動開賣時間", () => {
    const sale = "2026-10-01T12:00:00Z"
    expect(defaultTicketingTimeMs(sale, Date.now())).toBe(
      floorToMinute(Date.parse(sale))
    )
  })

  it("已經開賣的活動改用目前時間", () => {
    const sale = "2026-01-01T12:00:00Z"
    expect(defaultTicketingTimeMs(sale, Date.now())).toBe(
      floorToMinute(Date.now())
    )
  })

  it("開賣時間等於目前時間時使用目前時間", () => {
    expect(defaultTicketingTimeMs(NOW_ISO, Date.now())).toBe(
      floorToMinute(Date.now())
    )
  })

  it("沒有開賣時間就預設目前時間", () => {
    expect(defaultTicketingTimeMs(null, Date.now())).toBe(
      floorToMinute(Date.now())
    )
  })

  it("帶入值一律正規化到分鐘", () => {
    expect(defaultTicketingTimeLocal(null, Date.now())).toBe(
      msToLocalInput(floorToMinute(Date.now()))
    )
    expect(defaultTicketingTimeLocal(null, Date.now())).not.toContain(".")
  })
})

describe("isTicketingTimeInPast", () => {
  it("剛帶入的目前時間不算過期——秒數誤差不得造成假錯誤", () => {
    const seeded = defaultTicketingTimeLocal(null, Date.now())
    expect(isTicketingTimeInPast(seeded, Date.now())).toBe(false)
  })

  it("停留頁面跨過一分鐘後，原本帶入的時間就算過期", () => {
    const seeded = defaultTicketingTimeLocal(null, Date.now())
    vi.advanceTimersByTime(61_000)
    expect(isTicketingTimeInPast(seeded, Date.now())).toBe(true)
  })

  it("未來時間永遠不算過期", () => {
    const future = msToLocalInput(Date.now() + 3_600_000)
    expect(isTicketingTimeInPast(future, Date.now())).toBe(false)
  })

  it("無法解析的字串不當成過期（交給格式驗證處理）", () => {
    expect(isTicketingTimeInPast("not-a-time", Date.now())).toBe(false)
  })
})

describe("minTicketingTimeLocal", () => {
  it("min 對應目前這一分鐘的本地時間", () => {
    expect(minTicketingTimeLocal(Date.now())).toBe(
      msToLocalInput(floorToMinute(Date.now()))
    )
  })
})
