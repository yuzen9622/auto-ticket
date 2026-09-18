import { describe, expect, it } from "vitest"

import {
  formatCountdown,
  formatMs,
  relativeTime,
  parseServerDate,
  toOffsetIso,
} from "@/lib/format"
import { isWorkerOnline } from "@/lib/api/health"

describe("parseServerDate", () => {
  it("不帶時區的字串一律當作 UTC，避免 UTC+8 差 8 小時", () => {
    const naive = parseServerDate("2026-09-18T10:00:00")
    const explicit = parseServerDate("2026-09-18T10:00:00Z")
    expect(naive?.getTime()).toBe(explicit?.getTime())
  })

  it("帶位移的字串照原意解析", () => {
    expect(parseServerDate("2026-09-18T18:00:00+08:00")?.toISOString()).toBe(
      "2026-09-18T10:00:00.000Z"
    )
  })

  it("空值與無效值回 null", () => {
    expect(parseServerDate(null)).toBeNull()
    expect(parseServerDate("")).toBeNull()
    expect(parseServerDate("not-a-date")).toBeNull()
  })
})

describe("formatMs", () => {
  it("null 顯示破折號而非 0", () => {
    expect(formatMs(null)).toBe("—")
    expect(formatMs(undefined)).toBe("—")
    expect(formatMs(0)).toBe("0.00 ms")
  })

  it("依量級切換單位", () => {
    expect(formatMs(5.123)).toBe("5.12 ms")
    expect(formatMs(250)).toBe("250 ms")
    expect(formatMs(1500)).toBe("1.50 s")
  })
})

describe("formatCountdown", () => {
  it("固定寬度，不帶正負號", () => {
    expect(formatCountdown(3661_000)).toBe("01:01:01")
  })

  it("負數一律夾成 00:00:00，畫面不會出現負的倒數", () => {
    expect(formatCountdown(-5000)).toBe("00:00:00")
    expect(formatCountdown(-1)).toBe("00:00:00")
  })

  it("null 顯示佔位", () => {
    expect(formatCountdown(null)).toBe("--:--:--")
  })
})

describe("relativeTime", () => {
  const now = Date.parse("2026-09-18T10:00:00Z")

  it("null 回 never，文案交給字典", () => {
    expect(relativeTime(null, now)).toEqual({ unit: "never" })
  })

  it("各級距", () => {
    expect(relativeTime("2026-09-18T09:59:30Z", now)).toEqual({
      unit: "seconds",
      value: 30,
    })
    expect(relativeTime("2026-09-18T09:30:00Z", now)).toEqual({
      unit: "minutes",
      value: 30,
    })
    expect(relativeTime("2026-09-18T08:00:00Z", now)).toEqual({
      unit: "hours",
      value: 2,
    })
  })
})

describe("isWorkerOnline", () => {
  const now = Date.parse("2026-09-18T10:00:00Z")

  it("null 代表從未上線", () => {
    expect(isWorkerOnline(null, now)).toBe(false)
  })

  it("60 秒內視為線上", () => {
    expect(isWorkerOnline("2026-09-18T09:59:30Z", now)).toBe(true)
  })

  it("超過 60 秒視為離線", () => {
    expect(isWorkerOnline("2026-09-18T09:58:00Z", now)).toBe(false)
  })

  it("不帶時區的心跳也不會被誤判", () => {
    expect(isWorkerOnline("2026-09-18T09:59:30", now)).toBe(true)
  })
})

describe("toOffsetIso", () => {
  it("輸出一定帶時區位移（R13：排程不得差 8 小時）", () => {
    expect(toOffsetIso("2026-09-18T18:00")).toMatch(/[+-]\d{2}:\d{2}$/)
  })
})
