import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "./helpers/render"

const api = vi.hoisted(() => ({
  getAccountStatus: vi.fn(),
  storeCredentials: vi.fn(),
  eraseCredentials: vi.fn(),
}))
vi.mock("@/lib/api/accounts", () => ({
  getAccountStatus: api.getAccountStatus,
  storeCredentials: api.storeCredentials,
  eraseCredentials: api.eraseCredentials,
  getAccountJob: vi.fn(),
  requestLogin: vi.fn(),
  requestSessionCheck: vi.fn(),
  isTerminalJobState: () => true,
}))

import { AccountCard } from "@/components/settings/account-card"
import { CredentialForm } from "@/components/settings/credential-form"
import { LocalProfileManager } from "@/components/settings/local-profile-manager"
import { SessionJobWatcher } from "@/components/settings/session-job-watcher"

describe("Settings Components", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getAccountStatus.mockResolvedValue({
      platform: "kktix",
      source: "vault",
      configured: false,
      masked_account: null,
    })
  })

  afterEach(() => {
    document.body.style.pointerEvents = ""
  })

  describe("SessionJobWatcher", () => {
    it("does not render the removed session hint text", () => {
      renderWithProviders(<SessionJobWatcher platform="kktix" />)
      expect(
        screen.queryByText(/三項操作都會在執行程式端開啟瀏覽器/)
      ).not.toBeInTheDocument()
    })
  })

  describe("LocalProfileManager", () => {
    it("does not render the removed local profile notice", () => {
      renderWithProviders(<LocalProfileManager />)
      expect(
        screen.queryByText(/這些資料只存在這個瀏覽器/)
      ).not.toBeInTheDocument()
    })
  })

  describe("AccountCard", () => {
    it("renders AccountCard with platform select and shows platform status", async () => {
      renderWithProviders(<AccountCard platform="kktix" />)

      // 標題為「帳號狀態」，不寫死平台在標題
      expect(screen.getByText("帳號狀態")).toBeInTheDocument()
      expect(screen.queryByText("帳號狀態 · kktix")).not.toBeInTheDocument()

      // 等待狀態資料載入
      await waitFor(() => {
        expect(screen.getByText("KKTIX")).toBeInTheDocument()
      })
    })

    it("triggers onPlatformChange when platform is changed", async () => {
      const user = userEvent.setup()
      const onPlatformChange = vi.fn()
      renderWithProviders(
        <AccountCard platform="kktix" onPlatformChange={onPlatformChange} />
      )

      // 點擊 Select
      const selectTrigger = screen.getByRole("combobox")
      await user.click(selectTrigger)
      const option = await screen.findByRole("option", { name: "拓元售票" })
      await user.click(option)
      selectTrigger.blur()
      await waitFor(() => {
        expect(screen.queryByRole("option")).not.toBeInTheDocument()
      })

      expect(onPlatformChange).toHaveBeenCalledWith("tixcraft")
    })
  })

  describe("CredentialForm", () => {
    it("does not render the removed credential notice", () => {
      renderWithProviders(<CredentialForm platform="kktix" />)
      expect(
        screen.queryByText(/帳號與密碼只會送往本機後端的加密保管庫/)
      ).not.toBeInTheDocument()
    })

    it("submits credentials for specified platform", async () => {
      const user = userEvent.setup()
      api.storeCredentials.mockResolvedValue(undefined)

      renderWithProviders(<CredentialForm platform="tixcraft" />)

      // placeholder 包含 拓元售票
      expect(
        screen.getByPlaceholderText("請輸入 拓元售票 會員帳號")
      ).toBeInTheDocument()

      // 輸入帳號密碼並送出
      await user.type(
        screen.getByPlaceholderText("請輸入 拓元售票 會員帳號"),
        "test@example.com"
      )
      await user.type(
        screen.getByPlaceholderText("請輸入 拓元售票 會員密碼"),
        "password123"
      )

      const submitButton = screen.getByRole("button", { name: "儲存帳號密碼" })
      await user.click(submitButton)

      expect(api.storeCredentials).toHaveBeenCalledWith("tixcraft", {
        account: "test@example.com",
        access_key: "password123",
      })
    })
  })
})
