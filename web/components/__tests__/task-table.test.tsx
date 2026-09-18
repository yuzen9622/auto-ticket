import * as React from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "./helpers/render"

vi.mock("next/navigation", () => ({
  usePathname: () => "/tasks",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

const api = vi.hoisted(() => ({
  listTasks: vi.fn(),
  startTask: vi.fn(),
  cancelTask: vi.fn(),
  deleteTask: vi.fn(),
}))

vi.mock("@/lib/api/tasks", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/tasks")>(
    "@/lib/api/tasks"
  )
  return { ...actual, ...api }
})

import { TaskTable } from "@/components/tasks/task-table"
import { TaskRowActions } from "@/components/tasks/task-row-actions"

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "task_abc123",
    event_id: "ev_mayday01",
    status: "CREATED",
    execution_mode: "live",
    spec: { event_title: "五月天 2026 諾亞方舟" },
    scheduled_at: "2026-10-01T04:00:00Z",
    started_at: null,
    finished_at: null,
    error_message: null,
    created_at: "2026-09-18T10:00:00Z",
    ...overrides,
  }
}

beforeEach(() => {
  api.listTasks.mockReset()
  api.listTasks.mockResolvedValue({
    items: [task()],
    total: 1,
    limit: 50,
    offset: 0,
  })
})

describe("任務管理表格", () => {
  it("狀態徽章只顯示翻譯後的文字", async () => {
    renderWithProviders(<TaskTable />)
    expect(await screen.findByText("已建立")).toBeInTheDocument()
    expect(screen.queryByText("CREATED")).toBeNull()
    expect(screen.queryByText(/CREATED 已建立/)).toBeNull()
  })

  it("未知狀態顯示「未知狀態」而不是原始值", async () => {
    api.listTasks.mockResolvedValue({
      items: [task({ status: "SOMETHING_NEW" })],
      total: 1,
      limit: 50,
      offset: 0,
    })
    renderWithProviders(<TaskTable />)
    expect(await screen.findByText("未知狀態")).toBeInTheDocument()
    expect(screen.queryByText("SOMETHING_NEW")).toBeNull()
  })

  it("執行模式以自然語言顯示", async () => {
    renderWithProviders(<TaskTable />)
    expect(await screen.findByText("正式模式")).toBeInTheDocument()
    expect(screen.queryByText("live")).toBeNull()
  })

  it("狀態篩選顯示翻譯，但送給 API 的是原始 enum", async () => {
    const user = userEvent.setup()
    renderWithProviders(<TaskTable />)
    await screen.findByText("已建立")

    await user.click(screen.getByLabelText("依狀態篩選"))
    const option = await screen.findByRole("option", { name: "等待中" }).catch(
      () => null
    )
    expect(option).toBeNull() // 任務狀態沒有「等待中」，確認選單不是 job 狀態

    await user.click(await screen.findByRole("option", { name: "執行中" }))

    await waitFor(() =>
      expect(api.listTasks).toHaveBeenCalledWith(
        expect.objectContaining({ status: "RUNNING" })
      )
    )
  })

  it("「全部狀態」不會把哨兵值送給 API", async () => {
    renderWithProviders(<TaskTable />)
    await screen.findByText("已建立")
    expect(api.listTasks).toHaveBeenCalledWith(
      expect.objectContaining({ status: undefined })
    )
  })

  it("欄位標題都是自然語言", async () => {
    renderWithProviders(<TaskTable />)
    await screen.findByText("已建立")
    for (const header of ["任務編號", "活動", "狀態", "執行模式", "排程時間", "建立時間"]) {
      expect(screen.getByText(header)).toBeInTheDocument()
    }
    for (const forbidden of ["scheduled_at", "created_at", "event_title"]) {
      expect(screen.queryByText(forbidden)).toBeNull()
    }
  })

  it("新增任務連到首頁的搜尋入口", async () => {
    renderWithProviders(<TaskTable />)
    await screen.findByText("已建立")
    expect(screen.getByRole("link", { name: "新增任務" })).toHaveAttribute(
      "href",
      "/"
    )
  })

  it("不可刪除的任務用自然語言說明原因，不出現原始 enum", async () => {
    const user = userEvent.setup()
    const runningTask = task({ status: "RUNNING" })
    renderWithProviders(<TaskRowActions task={runningTask} />)

    const trigger = screen.getByRole("button", { name: "Open menu" })
    await user.click(trigger)
    const tip = await screen.findAllByText(
      "目前狀態無法刪除；只有已建立、已取消或失敗的任務可以刪除。"
    )
    expect(tip.length).toBeGreaterThan(0)
    expect(screen.queryByText(/僅 CREATED/)).toBeNull()
  })
})
