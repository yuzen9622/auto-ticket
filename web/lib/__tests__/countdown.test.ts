import { describe, expect, it } from "vitest"

import { deriveCountdown } from "@/lib/countdown"
import type { ClockTickPayload } from "@/lib/ws/types"

const RECEIVED_AT = 1_000_000

function tick(patch: Partial<ClockTickPayload>): ClockTickPayload {
  return {
    server_time: "2026-09-18T10:00:00Z",
    phase: "waiting_for_sale",
    time_to_sale_ms: 30_000,
    time_to_timeout_ms: null,
    clock_offset_ms: 0,
    ...patch,
  }
}

describe("deriveCountdown", () => {
  it("還沒收到時鐘訊息時沒有階段", () => {
    expect(deriveCountdown(null, RECEIVED_AT, RECEIVED_AT)).toEqual({
      phase: null,
      remainingMs: null,
    })
  })

  it("開賣前數的是距離開賣的時間", () => {
    const view = deriveCountdown(tick({}), RECEIVED_AT, RECEIVED_AT)
    expect(view.phase).toBe("waiting_for_sale")
    expect(view.remainingMs).toBe(30_000)
  })

  it("開賣後改數本次搶票的剩餘時間，不再用過期的開賣倒數", () => {
    const view = deriveCountdown(
      tick({
        phase: "ticketing",
        time_to_sale_ms: 0,
        time_to_timeout_ms: 90_000,
      }),
      RECEIVED_AT,
      RECEIVED_AT
    )
    expect(view.phase).toBe("ticketing")
    expect(view.remainingMs).toBe(90_000)
  })

  it("兩則訊息之間用本地時間補秒數", () => {
    const view = deriveCountdown(tick({}), RECEIVED_AT + 5_000, RECEIVED_AT)
    expect(view.remainingMs).toBe(25_000)
  })

  it("補過頭也不會變成負數", () => {
    const view = deriveCountdown(tick({}), RECEIVED_AT + 999_000, RECEIVED_AT)
    expect(view.remainingMs).toBe(0)
  })

  it("終態停止倒數", () => {
    const view = deriveCountdown(
      tick({ phase: "finished", time_to_sale_ms: null }),
      RECEIVED_AT + 10_000,
      RECEIVED_AT
    )
    expect(view).toEqual({ phase: "finished", remainingMs: null })
  })

  it("搶票階段但逾時時間未知時不硬湊數字", () => {
    const view = deriveCountdown(
      tick({ phase: "ticketing", time_to_sale_ms: 0, time_to_timeout_ms: null }),
      RECEIVED_AT,
      RECEIVED_AT
    )
    expect(view.remainingMs).toBeNull()
  })
})
