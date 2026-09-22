import * as React from "react"
import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "./helpers/render"
import { ClockPanel } from "@/components/console/clock-panel"
import { StateRail } from "@/components/console/state-rail"
import { AutomationStatusBanner } from "@/components/console/automation-status-banner"
import { HumanGateBanner } from "@/components/console/human-gate-banner"
import type { ClockTickPayload, HumanGateLogPayload } from "@/lib/ws/types"
import type { AutomationStatus } from "@/lib/ws/use-task-socket"

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

  it("不顯示時間校正差與伺服器時間這類內部資訊", () => {
    renderPanel(tick({}))
    expect(screen.queryByText("時間校正差")).toBeNull()
    expect(screen.queryByText("伺服器時間")).toBeNull()
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

describe("自動化執行狀態橫幅與互斥", () => {
  it("收到 cloudflare_grace 顯示「正在自動完成安全驗證」且 role 為 status", () => {
    const status: AutomationStatus = {
      phase: "cloudflare_grace",
      page_kind: "CHALLENGE",
      elapsed_s: 4.2,
      budget_s: 45,
      round: 1,
      max_rounds: 3,
    }
    renderWithProviders(<AutomationStatusBanner status={status} />)
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.getByText("正在自動完成安全驗證")).toBeInTheDocument()
    expect(screen.getByText(/已等待 4.2 秒 \/ 上限 45 秒/)).toBeInTheDocument()
  })

  it("收到 ocr_processing 顯示「正在辨識驗證碼（2/5）」", () => {
    const status: AutomationStatus = {
      phase: "ocr_processing",
      attempt: 2,
      max_retries: 5,
    }
    renderWithProviders(<AutomationStatusBanner status={status} />)
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.getByText("正在辨識驗證碼（2/5）")).toBeInTheDocument()
    expect(
      screen.getByText(
        "辨識不成功時會自動換一張新的驗證碼再試，達到次數上限才會請你接手。"
      )
    ).toBeInTheDocument()
  })

  it("收到 verification_completed 顯示「驗證完成，繼續購票」", () => {
    const status: AutomationStatus = {
      phase: "verification_completed",
      kind: "image_captcha",
    }
    renderWithProviders(<AutomationStatusBanner status={status} />)
    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.getByText("驗證完成，繼續購票")).toBeInTheDocument()
  })

  it("隨後收到 waiting_for_human 時互斥呈現 human gate 橫幅", () => {
    const automationStatus: AutomationStatus = {
      phase: "cloudflare_grace",
      page_kind: "CHALLENGE",
      elapsed_s: 45,
      budget_s: 45,
      round: 1,
      max_rounds: 3,
    }
    const gate: HumanGateLogPayload = {
      phase: "waiting_for_human",
      page_kind: "CHALLENGE",
      attempt: 1,
      hint: "自動處理失敗，請在 Chrome 完成驗證（本程式不會代為繞過）",
      event_url: "https://example.test",
      attended: true,
      can_clear_bot_check: true,
    }

    const { rerender } = renderWithProviders(
      <>
        <AutomationStatusBanner status={automationStatus} />
        <HumanGateBanner gate={null} />
      </>
    )
    expect(screen.getByText("正在自動完成安全驗證")).toBeInTheDocument()

    rerender(
      <>
        <AutomationStatusBanner status={null} />
        <HumanGateBanner gate={gate} />
      </>
    )
    expect(screen.queryByText("正在自動完成安全驗證")).toBeNull()
    expect(
      screen.getByText("自動處理失敗，請在 Chrome 完成驗證（本程式不會代為繞過）")
    ).toBeInTheDocument()
  })

  it("自動狀態橫幅內沒有任何 button", () => {
    const status: AutomationStatus = {
      phase: "cloudflare_grace",
      page_kind: "CHALLENGE",
      elapsed_s: 2,
      budget_s: 45,
      round: 1,
      max_rounds: 3,
    }
    renderWithProviders(<AutomationStatusBanner status={status} />)
    expect(screen.queryByRole("button")).toBeNull()
  })
})

describe("購票進度", () => {
  const PHASES = ["準備中", "等待開賣", "選票", "填單付款"]

  it("只列出五個階段，不把 14 個內部狀態攤開", () => {
    renderWithProviders(
      <StateRail currentState="TICKET_SELECTION" visitedStates={["PREPARING"]} />
    )
    expect(screen.getAllByRole("listitem")).toHaveLength(5)
    for (const phase of [...PHASES, "結束"]) {
      expect(screen.getByText(phase)).toBeInTheDocument()
    }
    for (const internal of ["選擇票種", "選擇座位", "填寫資料", "需要驗證"]) {
      expect(screen.queryByText(internal)).toBeNull()
    }
  })

  it("目前狀態所屬的階段標成目前步驟", () => {
    renderWithProviders(
      <StateRail
        currentState="PAYMENT_PROCESSING"
        visitedStates={["PREPARING", "SALE_OPEN", "FORM_FILLING"]}
      />
    )
    expect(screen.getByText("填單付款").closest("li")).toHaveAttribute(
      "aria-current",
      "step"
    )
  })

  it("結束後最後一格改寫成實際結果", () => {
    renderWithProviders(
      <StateRail currentState="SOLD_OUT" visitedStates={["SALE_OPEN"]} />
    )
    expect(screen.getByText("已售罄")).toBeInTheDocument()
    expect(screen.queryByText("結束")).toBeNull()
  })
})
