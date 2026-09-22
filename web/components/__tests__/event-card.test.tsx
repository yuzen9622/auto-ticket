import * as React from "react"
import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "./helpers/render"
import { EventCard } from "@/components/events/event-card"

function event(overrides: Record<string, unknown> = {}) {
  return {
    id: "ev_demo",
    title: "測試活動",
    description: "測試用的活動說明",
    organizer: "測試主辦",
    ticketing_providers: [
      { id: "kktix", name: "KKTIX", event_url: "https://x.kktix.cc/events/y" },
    ],
    canonical_url: "https://x.kktix.cc/events/y",
    sale_start_at: null,
    sale_end_at: null,
    event_start_at: null,
    status: "UNKNOWN",
    detail_loaded: false,
    ...overrides,
  }
}

function statusCell(): HTMLElement {
  const cell = screen.getByText("活動狀態").parentElement?.querySelector("dd")
  if (!cell) throw new Error("找不到活動狀態欄位")
  return cell as HTMLElement
}

describe("活動卡片的票況呈現", () => {
  it("票況還在確認時只放骨架動畫，不放任何看得到的文字", () => {
    renderWithProviders(<EventCard event={event()} />)

    const cell = statusCell()
    expect(cell).toHaveAttribute("aria-busy", "true")
    expect(cell.querySelector("[data-slot='skeleton']")).not.toBeNull()
    // 骨架期間唯一的文字是給讀螢幕的人用的，視覺上看不到。
    expect(cell.querySelector(".sr-only")).toHaveTextContent("票況確認中…")
    expect(cell.textContent).not.toContain("狀態未確認")
  })

  it("票況確認完就換成狀態文字，骨架收掉", () => {
    renderWithProviders(
      <EventCard event={event({ status: "ON_SALE", detail_loaded: true })} />
    )

    const cell = statusCell()
    expect(cell).not.toHaveAttribute("aria-busy")
    expect(cell.querySelector("[data-slot='skeleton']")).toBeNull()
    expect(cell).toHaveTextContent("熱賣中")
  })

  it("等不到票況時收掉骨架、如實說狀態未確認", () => {
    // 沒有 Worker 在跑時票況永遠不會被確認；骨架一直轉下去看起來像壞掉。
    renderWithProviders(
      <EventCard event={event()} awaitingStatus={false} />
    )

    const cell = statusCell()
    expect(cell.querySelector("[data-slot='skeleton']")).toBeNull()
    expect(cell).toHaveTextContent("狀態未確認")
  })
})
