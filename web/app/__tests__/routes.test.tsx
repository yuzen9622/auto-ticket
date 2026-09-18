import * as React from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"

import { renderWithProviders } from "@/components/__tests__/helpers/render"

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

const api = vi.hoisted(() => ({ searchEvents: vi.fn(), listTasks: vi.fn() }))
vi.mock("@/lib/api/events", () => ({ searchEvents: api.searchEvents }))
vi.mock("@/lib/api/tasks", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/tasks")>(
    "@/lib/api/tasks"
  )
  return { ...actual, listTasks: api.listTasks }
})

import NewTaskEntryPage from "@/app/page"
import TaskManagementPage from "@/app/tasks/page"

beforeEach(() => {
  api.searchEvents.mockReset()
  api.listTasks.mockReset()
  api.listTasks.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 })
})

describe("首頁 /", () => {
  it("顯示活動搜尋，不顯示任務管理表格", async () => {
    renderWithProviders(<NewTaskEntryPage />)

    expect(await screen.findByText("搜尋活動")).toBeInTheDocument()
    expect(
      screen.getByLabelText("活動名稱、關鍵字或活動網址")
    ).toBeInTheDocument()

    // 任務管理的欄位標題不該出現在首頁。
    expect(screen.queryByText("任務管理")).toBeNull()
    expect(screen.queryByText("排程時間")).toBeNull()
    expect(screen.queryByText("任務編號")).toBeNull()
    expect(api.listTasks).not.toHaveBeenCalled()
  })

  it("首頁沒有步驟式精靈的痕跡", async () => {
    renderWithProviders(<NewTaskEntryPage />)
    await screen.findByText("搜尋活動")
    expect(screen.queryByText(/步驟\s*\d\s*\/\s*3/)).toBeNull()
    expect(screen.queryByText("活動解析")).toBeNull()
    expect(screen.queryByRole("button", { name: "下一步" })).toBeNull()
  })
})

describe("任務管理 /tasks", () => {
  it("顯示任務管理介面", async () => {
    renderWithProviders(<TaskManagementPage />)

    expect(await screen.findByText("任務管理")).toBeInTheDocument()
    await waitFor(() => expect(api.listTasks).toHaveBeenCalled())
    expect(screen.getByText("尚無任務")).toBeInTheDocument()
    expect(screen.queryByLabelText("活動名稱、關鍵字或活動網址")).toBeNull()
  })
})
