import * as React from "react"
import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "./helpers/render"
import { ClockPanel } from "@/components/console/clock-panel"
import type { ClockTickPayload } from "@/lib/ws/types"

const RECEIVED_AT = Date.now()

function tick(patch: Partial<ClockTickPayload>): ClockTickPayload {
  return {
    server_time: "2026-09-18T10:00:00Z",
    phase: "waiting_for_sale",
    time_to_sale_ms: 125_000,
    time_to_timeout_ms: null,
    clock_offset_ms: 12,
    ...patch,
  }
}

function renderPanel(clock: ClockTickPayload | null, resultLabel = "任務已結束") {
  return renderWithProviders(
    <ClockPanel
      clock={clock}
      clockReceivedAt={RECEIVED_AT}
      resultLabel={resultLabel}
    />
  )
}

describe("倒數面板", () => {
  it("開賣前顯示「開賣倒數」", () => {
    renderPanel(tick({}))
    expect(screen.getByText("開賣倒數")).toBeInTheDocument()
    expect(screen.queryByText("搶票倒數")).toBeNull()
  })

  it("開賣後顯示「搶票倒數」，數的是剩餘搶票時間", () => {
    renderPanel(
      tick({ phase: "ticketing", time_to_sale_ms: 0, time_to_timeout_ms: 90_000 })
    )
    expect(screen.getByText("搶票倒數")).toBeInTheDocument()
    expect(screen.queryByText("開賣倒數")).toBeNull()
  })

  it("終態停止倒數並改說自然語言結果", () => {
    renderPanel(
      tick({ phase: "finished", time_to_sale_ms: null, time_to_timeout_ms: null }),
      "搶票完成"
    )
    expect(screen.getByText("倒數已結束")).toBeInTheDocument()
    expect(screen.getByText("搶票完成")).toBeInTheDocument()
    expect(screen.queryByText("00:00:00")).toBeNull()
  })

  it("倒數數字不會出現負號", () => {
    renderPanel(tick({ time_to_sale_ms: 0 }))
    expect(document.body.textContent).not.toMatch(/-\d{2}:\d{2}:\d{2}/)
  })

  it("標題與欄位都是自然語言，不出現 snake_case", () => {
    renderPanel(tick({}))
    expect(screen.getByText("距離開賣")).toBeInTheDocument()
    expect(screen.getByText("剩餘搶票時間")).toBeInTheDocument()
    expect(screen.getByText("時間校正差")).toBeInTheDocument()
    expect(screen.getByText("伺服器時間")).toBeInTheDocument()
    for (const forbidden of [
      "time_to_sale",
      "time_to_timeout",
      "clock_offset",
      "server_time",
      "CLOCK_TICK",
    ]) {
      expect(screen.queryByText(forbidden)).toBeNull()
    }
  })

  it("還沒收到時鐘訊息時給中文說明而不是空白", () => {
    renderPanel(null)
    expect(
      screen.getByText("任務開始執行後才會顯示倒數。")
    ).toBeInTheDocument()
  })

  it("重連後由伺服器的階段決定顯示，不靠前端猜", () => {
    // 重連時第一則就是伺服器算好的快照；階段是 ticketing 就直接顯示搶票倒數。
    const { unmount } = renderPanel(tick({ phase: "waiting_for_sale" }))
    expect(screen.getByText("開賣倒數")).toBeInTheDocument()
    unmount()

    renderPanel(
      tick({ phase: "ticketing", time_to_sale_ms: 0, time_to_timeout_ms: 45_000 })
    )
    expect(screen.getByText("搶票倒數")).toBeInTheDocument()
  })
})
