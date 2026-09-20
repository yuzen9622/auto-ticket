import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { BrowserProfileManager } from "@/components/settings/browser-profile-manager"
import { renderWithProviders } from "./helpers/render"

describe("BrowserProfileManager", () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.clearAllMocks()
  })

  it("渲染預設 profile (live) 且不顯示刪除按鈕", async () => {
    const onProfileChange = vi.fn()
    renderWithProviders(
      <BrowserProfileManager profile="live" onProfileChange={onProfileChange} />
    )

    await waitFor(() => {
      expect(screen.getByText(/live \(預設\)/)).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/刪除設定檔/)).not.toBeInTheDocument()
  })

  it("點擊新增設定檔能成功新增並切換", async () => {
    const user = userEvent.setup()
    const onProfileChange = vi.fn()
    renderWithProviders(
      <BrowserProfileManager profile="live" onProfileChange={onProfileChange} />
    )

    await user.click(screen.getByRole("button", { name: "新增設定檔" }))

    const input = screen.getByPlaceholderText("輸入新設定檔名稱（例如 account2）")
    await user.type(input, "account_b")
    await user.click(screen.getByRole("button", { name: "新增" }))

    expect(onProfileChange).toHaveBeenCalledWith("account_b")
  })

  it("自訂 profile 顯示刪除按鈕，刪除後切回 live", async () => {
    const user = userEvent.setup()
    const onProfileChange = vi.fn()
    renderWithProviders(
      <BrowserProfileManager profile="account_b" onProfileChange={onProfileChange} />
    )

    const deleteBtn = screen.getByRole("button", { name: "刪除設定檔 account_b" })
    await user.click(deleteBtn)

    expect(onProfileChange).toHaveBeenCalledWith("live")
  })
})
