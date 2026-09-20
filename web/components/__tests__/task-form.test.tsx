import * as React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "./helpers/render"

const nav = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock("next/navigation", () => ({
  usePathname: () => "/tasks/ev_mayday01/new",
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

const api = vi.hoisted(() => ({
  getEvent: vi.fn(),
  createTask: vi.fn(),
  getAccountStatus: vi.fn(),
}))
vi.mock("@/lib/api/events", () => ({ getEvent: api.getEvent }))
vi.mock("@/lib/api/tasks", () => ({ createTask: api.createTask }))
vi.mock("@/lib/api/accounts", () => ({ getAccountStatus: api.getAccountStatus }))

import { ApiError } from "@/lib/api/client"
import { TaskForm } from "@/components/tasks/task-form"

/** 固定系統時間：時間相關斷言不能隨真實時鐘漂移。 */
const NOW = new Date("2026-09-18T10:30:45.500Z")

function eventFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "ev_mayday01",
    platform: "kktix",
    organizer: "believe",
    organizer_name: "相信音樂",
    event_slug: "mayday-2026",
    title: "五月天 2026 諾亞方舟",
    description: "五月天世界巡迴演唱會台北站",
    canonical_url: "https://believe.kktix.cc/events/mayday-2026",
    ticketing_providers: [
      { id: "kktix", name: "KKTIX", event_url: "https://believe.kktix.cc/events/mayday-2026" },
    ],
    status: "ANNOUNCED",
    sale_start_at: "2026-10-01T04:00:00Z",
    sale_end_at: null,
    event_start_at: "2026-12-24T11:00:00Z",
    ticket_types: [],
    detail_loaded: true,
    raw_metadata: null,
    ...overrides,
  }
}

async function fillContact(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("聯絡人姓名"), "王小明")
  await user.type(screen.getByLabelText("聯絡人電話"), "0912345678")
  await user.type(screen.getByLabelText("聯絡人 Email"), "user@example.test")
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(NOW)
  nav.push.mockClear()
  api.getEvent.mockReset()
  api.createTask.mockReset()
  api.getAccountStatus.mockReset()
  api.getAccountStatus.mockResolvedValue({
    platform: "kktix",
    source: "vault",
    configured: true,
    masked_account: "us***er@example.test",
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe("任務表單的活動載入", () => {
  it("以 eventId 重新取得活動，不依賴上一頁的狀態", async () => {
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await waitFor(() =>
      expect(api.getEvent).toHaveBeenCalledWith("ev_mayday01", expect.anything())
    )
    expect(await screen.findByText("五月天 2026 諾亞方舟")).toBeInTheDocument()
    expect(screen.getByText("相信音樂")).toBeInTheDocument()
  })

  it("活動不存在時顯示中文錯誤並提供返回搜尋", async () => {
    api.getEvent.mockRejectedValue(new ApiError(404, "not_found", "Event not found"))
    renderWithProviders(<TaskForm eventId="ev_missing" />)

    expect(await screen.findByText("找不到這個活動")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "返回搜尋活動" })).toHaveAttribute(
      "href",
      "/"
    )
    expect(screen.queryByText(/Event not found/)).toBeNull()
  })

  it("活動載入失敗時顯示可重試的說明", async () => {
    api.getEvent.mockRejectedValue(new ApiError(0, "network_error", "boom"))
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)
    expect(await screen.findByText("無法載入活動資料")).toBeInTheDocument()
  })

  it("活動資料不完整時提醒使用者確認", async () => {
    api.getEvent.mockResolvedValue(
      eventFixture({ detail_loaded: false, sale_start_at: null, ticket_types: [] })
    )
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)
    expect(
      await screen.findByText(
        "這個活動的部分資料尚未取得，請確認下方欄位後再送出。"
      )
    ).toBeInTheDocument()
  })
})

describe("搶票時間自動帶入", () => {
  it("未來開賣的活動帶入活動開賣時間", async () => {
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    const input = await screen.findByLabelText("搶票時間")
    const expected = new Date("2026-10-01T04:00:00Z")
    const pad = (n: number) => String(n).padStart(2, "0")
    expect(input).toHaveValue(
      `${expected.getFullYear()}-${pad(expected.getMonth() + 1)}-${pad(
        expected.getDate()
      )}T${pad(expected.getHours())}:${pad(expected.getMinutes())}`
    )
  })

  it("已經開賣的活動不問搶票時間，改為立即執行", async () => {
    api.getEvent.mockResolvedValue(
      eventFixture({ status: "ON_SALE", sale_start_at: "2026-01-01T00:00:00Z" })
    )
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    expect(await screen.findByLabelText("執行時機")).toHaveTextContent(
      "立即執行"
    )
    expect(screen.queryByLabelText("搶票時間")).toBeNull()
  })

  it("狀態還沒更新、但開賣時間已過的活動同樣立即執行", async () => {
    api.getEvent.mockResolvedValue(
      eventFixture({ status: "ANNOUNCED", sale_start_at: "2026-01-01T00:00:00Z" })
    )
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    expect(await screen.findByLabelText("執行時機")).toHaveTextContent(
      "立即執行"
    )
  })

  it("沒有開賣時間的活動帶目前時間並顯示提醒", async () => {
    api.getEvent.mockResolvedValue(eventFixture({ sale_start_at: null }))
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    expect(
      await screen.findByText("未取得活動開賣時間，請確認搶票時間。")
    ).toBeInTheDocument()
  })

  it("datetime-local 的 min 對應目前本地時間", async () => {
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    const input = await screen.findByLabelText("搶票時間")
    const pad = (n: number) => String(n).padStart(2, "0")
    expect(input).toHaveAttribute(
      "min",
      `${NOW.getFullYear()}-${pad(NOW.getMonth() + 1)}-${pad(
        NOW.getDate()
      )}T${pad(NOW.getHours())}:${pad(NOW.getMinutes())}`
    )
  })

  it("使用者改過時間後，同一場活動的重新抓取不會覆蓋", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    const { client } = renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    const input = await screen.findByLabelText("搶票時間")
    await user.clear(input)
    await user.type(input, "2026-11-11T20:00")
    expect(input).toHaveValue("2026-11-11T20:00")

    await client.refetchQueries({ queryKey: ["event", "ev_mayday01"] })
    await waitFor(() => expect(api.getEvent).toHaveBeenCalledTimes(2))
    expect(screen.getByLabelText("搶票時間")).toHaveValue("2026-11-11T20:00")
  })
})

describe("表單驗證與送出", () => {
  it("欄位無效時阻止進入確認狀態、不送 API，並把焦點移到第一個錯誤欄位", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))

    expect(api.createTask).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: "送出任務" })).toBeNull()
    expect(await screen.findByText("請填寫聯絡人姓名。")).toBeInTheDocument()
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("聯絡人姓名"))
    )
  })

  it("指定區域策略沒有區域時在欄位旁顯示錯誤", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)

    await user.click(screen.getByLabelText("座位策略"))
    await user.click(await screen.findByRole("option", { name: "指定區域" }))
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))

    expect(
      await screen.findByText("選擇「指定區域」時，至少要填一個區域。")
    ).toBeInTheDocument()
  })

  it("完整填寫後可以進入確認畫面，確認畫面沒有 snake_case 欄位名", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))

    expect(
      await screen.findByRole("button", { name: "送出任務" })
    ).toBeInTheDocument()

    for (const forbidden of [
      "event_title",
      "event_url",
      "payment_method",
      "quantity",
      "priorities",
      "seat_strategy",
      "contact.name",
      "contact.phone",
      "contact.email",
      "attendee[0]",
      "verification_rules",
      "timeout_seconds",
      "best_available",
      "max_retries",
      "qualification_code",
      "auto_login",
    ]) {
      expect(screen.queryByText(forbidden)).toBeNull()
    }
    // 步驟式精靈的痕跡完全不存在。
    expect(screen.queryByText(/步驟\s*\d\s*\/\s*3/)).toBeNull()
    expect(screen.queryByRole("button", { name: "下一步" })).toBeNull()
    expect(screen.queryByRole("button", { name: "上一步" })).toBeNull()
  })

  it("電話遮蔽後才顯示在確認畫面", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))

    await screen.findByRole("button", { name: "送出任務" })
    expect(screen.queryByText(/0912345678/)).toBeNull()
  })

  it("送出的搶票時間是含時區位移的 ISO 8601，模式維持正式模式", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    api.createTask.mockResolvedValue({ id: "task_abc", status: "SCHEDULED" })
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))
    await user.click(await screen.findByRole("button", { name: "送出任務" }))

    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1))
    const body = api.createTask.mock.calls[0][0]
    expect(body.sale_start_at).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/
    )
    expect(body.execution_mode).toBe("live")
    expect(body).not.toHaveProperty("payment_method")
    expect(JSON.stringify(body)).not.toContain("card")
    expect(nav.push).toHaveBeenCalledWith("/tasks/task_abc")
  })

  it("已在販售的活動不送搶票時間，由後端直接執行", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture({ status: "ON_SALE" }))
    api.createTask.mockResolvedValue({ id: "task_abc", status: "SCHEDULED" })
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("執行時機")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))
    await user.click(await screen.findByRole("button", { name: "送出任務" }))

    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1))
    expect(api.createTask.mock.calls[0][0].sale_start_at).toBeNull()
  })

  it("停留頁面直到搶票時間過期後，送出時會被重新驗證擋下", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture({ sale_start_at: null }))
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))
    await screen.findByRole("button", { name: "送出任務" })

    // 使用者停在確認畫面，時間一路走到帶入的那一分鐘之後。
    vi.setSystemTime(new Date(NOW.getTime() + 5 * 60_000))
    await user.click(screen.getByRole("button", { name: "送出任務" }))

    expect(api.createTask).not.toHaveBeenCalled()
    expect(
      await screen.findByText("搶票時間不能早於目前時間，請重新選擇。")
    ).toBeInTheDocument()
  })
})

describe("執行模式", () => {
  it("預設是正式模式，並顯示正式模式說明", async () => {
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    expect(await screen.findByLabelText("執行模式")).toHaveTextContent("正式模式")
    expect(
      screen.getByText("目前使用正式模式，會以你設定的帳號進行真實搶票。")
    ).toBeInTheDocument()
    expect(screen.queryByText(/MOCK 測試模式/)).toBeNull()
    expect(screen.queryByText(/不會發生真實付款/)).toBeNull()
  })

  it("可以明確切換到測試模式", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    api.createTask.mockResolvedValue({ id: "task_abc", status: "SCHEDULED" })
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByLabelText("執行模式"))
    await user.click(await screen.findByRole("option", { name: "測試模式" }))
    expect(screen.getByText("目前使用測試模式")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))
    await user.click(await screen.findByRole("button", { name: "送出任務" }))

    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1))
    expect(api.createTask.mock.calls[0][0].execution_mode).toBe("mock")
  })

  it("正式模式缺少帳號設定時擋下送出並導向設定頁", async () => {
    const user = userEvent.setup()
    api.getAccountStatus.mockResolvedValue({
      platform: "kktix",
      source: "none",
      configured: false,
      masked_account: null,
    })
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))

    expect(api.createTask).not.toHaveBeenCalled()
    expect(
      await screen.findByText("正式模式需要先在設定頁完成帳號設定。")
    ).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "前往設定頁" })).toHaveAttribute(
      "href",
      "/settings"
    )
  })

  it("表單沒有任何信用卡輸入欄位", async () => {
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    for (const pattern of [/卡號/, /信用卡/, /安全碼/, /CVV/i, /有效期/]) {
      expect(screen.queryByLabelText(pattern)).toBeNull()
      expect(screen.queryByText(pattern)).toBeNull()
    }
  })
})

describe("自動化設定與進階設定", () => {
  it("預設送出的 payload 含 7 個欄位且為預設值", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    api.createTask.mockResolvedValue({ id: "task_abc", status: "SCHEDULED" })
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)
    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))
    await user.click(await screen.findByRole("button", { name: "送出任務" }))

    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1))
    const payload = api.createTask.mock.calls[0][0]
    expect(payload.auto_cloudflare).toBe(true)
    expect(payload.auto_ocr).toBe(true)
    expect(payload.auto_submit_verification).toBe(true)
    expect(payload.ocr_model_path).toBeNull()
    expect(payload.ocr_max_retries).toBe(5)
    expect(payload.cloudflare_max_retries).toBe(3)
    expect(payload.debug_screenshots_and_logs).toBe(false)
  })

  it("關掉一般開關並改動進階欄位後，送出的 payload 逐欄相符", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    api.createTask.mockResolvedValue({ id: "task_abc", status: "SCHEDULED" })
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)

    // 切換一般開關
    await user.click(screen.getByLabelText("自動處理安全驗證"))
    await user.click(screen.getByLabelText("自動辨識圖片驗證碼"))
    await user.click(screen.getByLabelText("辨識後自動送出"))

    // 修改進階欄位
    const ocrRetries = screen.getByLabelText("驗證碼最大嘗試次數")
    await user.clear(ocrRetries)
    await user.type(ocrRetries, "8")

    const cfRetries = screen.getByLabelText("安全驗證最大等待次數")
    await user.clear(cfRetries)
    await user.type(cfRetries, "1")

    await user.click(screen.getByLabelText("Debug 截圖與詳細記錄"))

    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))
    await user.click(await screen.findByRole("button", { name: "送出任務" }))

    await waitFor(() => expect(api.createTask).toHaveBeenCalledTimes(1))
    const payload = api.createTask.mock.calls[0][0]
    expect(payload.auto_cloudflare).toBe(false)
    expect(payload.auto_ocr).toBe(false)
    expect(payload.auto_submit_verification).toBe(false)
    expect(payload.ocr_model_path).toBeNull()
    expect(payload.ocr_max_retries).toBe(8)
    expect(payload.cloudflare_max_retries).toBe(1)
    expect(payload.debug_screenshots_and_logs).toBe(true)
  })

  it("ocr_max_retries 填 0 顯示錯誤且不送出", async () => {
    const user = userEvent.setup()
    api.getEvent.mockResolvedValue(eventFixture())
    renderWithProviders(<TaskForm eventId="ev_mayday01" />)

    await screen.findByLabelText("搶票時間")
    await fillContact(user)

    const ocrRetries = screen.getByLabelText("驗證碼最大嘗試次數")
    await user.clear(ocrRetries)
    await user.type(ocrRetries, "0")

    await user.click(screen.getByRole("button", { name: "確認搶票資訊" }))

    expect(api.createTask).not.toHaveBeenCalled()
    expect(
      await screen.findByText("驗證碼最大嘗試次數必須介於 1 到 20 次。")
    ).toBeInTheDocument()
  })
})
