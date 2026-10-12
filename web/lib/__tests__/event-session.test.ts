import { describe, expect, it } from "vitest"
import type { EventOut } from "@/lib/api/types"
import { sessionPreference, sessionSale } from "@/lib/event-session"

const event = {
  status: "ON_SALE", sale_start_at: "2026-01-01T00:00:00Z",
  raw_metadata: { sessions: [
    { key: "A", name: "同名", can_buy: true, sale_start_at: "2026-01-01T00:00:00Z" },
    { key: "B", name: "同名", can_buy: false, sale_start_at: "2026-10-10T04:00:00Z" },
  ] },
} as EventOut

describe("session identity and schedule", () => {
  it("keeps duplicate names distinct and uses the unopened session time", () => {
    expect(sessionPreference(event.raw_metadata!.sessions![1])).toBe("id:B")
    expect(sessionSale(event, "id:B", Date.parse("2026-10-08T00:00:00Z"))).toEqual({
      selling: false, saleStartAt: "2026-10-10T04:00:00Z",
    })
    expect(sessionSale(event, "id:A", Date.now()).selling).toBe(true)
  })
  it("uses the single session window instead of the expired event window", () => {
    const single = { ...event, raw_metadata: { sessions: [event.raw_metadata!.sessions![1]] } }
    expect(sessionSale(single, "", Date.now()).saleStartAt).toBe("2026-10-10T04:00:00Z")
    expect(sessionSale(single, "", Date.now()).selling).toBe(false)
  })
})
