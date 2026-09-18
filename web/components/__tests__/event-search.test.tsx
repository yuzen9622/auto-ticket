import * as React from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "./helpers/render"

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  params: new URLSearchParams(),
}))

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
  useSearchParams: () => nav.params,
}))

const api = vi.hoisted(() => ({ searchEvents: vi.fn() }))
vi.mock("@/lib/api/events", () => ({ searchEvents: api.searchEvents }))

import { ApiError } from "@/lib/api/client"
import { EventSearch } from "@/components/events/event-search"

function result(overrides: Record<string, unknown> = {}) {
  return {
    id: "ev_mayday01",
    title: "五月天 2026 諾亞方舟",
    description: "五月天世界巡迴演唱會台北站",
    organizer: "相信音樂",
    ticketing_providers: [
      {
        id: "kktix",
        name: "KKTIX",
        event_url: "https://believe.kktix.cc/events/mayday-2026",
      },
    ],
    canonical_url: "https://believe.kktix.cc/events/mayday-2026",
    sale_start_at: "2026-10-01T04:00:00Z",
    event_start_at: "2026-12-24T11:00:00Z",
    status: "ANNOUNCED",
    ...overrides,
  }
}

beforeEach(() => {
  nav.push.mockClear()
  nav.params = new URLSearchParams()
  api.searchEvents.mockReset()
})

describe("活動搜尋", () => {
  it("初始狀態不呼叫 API，也不顯示任務表格", () => {
    renderWithProviders(<EventSearch />)
    expect(api.searchEvents).not.toHaveBeenCalled()
    expect(screen.getByText("還沒有開始搜尋")).toBeInTheDocument()
    expect(screen.queryByText("排程時間")).toBeNull()
  })

  it("送出搜尋後把查詢寫進網址的 q，並正確編碼", async () => {
    const user = userEvent.setup()
    renderWithProviders(<EventSearch />)

    await user.type(
      screen.getByLabelText("活動名稱、關鍵字或活動網址"),
      "五月天"
    )
    await user.click(screen.getByRole("button", { name: "搜尋" }))

    expect(nav.push).toHaveBeenCalledTimes(1)
    const target = nav.push.mock.calls[0][0] as string
    expect(target).toBe(`/?q=${encodeURIComponent("五月天")}`)
    expect(target).toContain("%E4%BA%94%E6%9C%88%E5%A4%A9")
  })

  it("空白搜尋不得呼叫 API", async () => {
    const user = userEvent.setup()
    renderWithProviders(<EventSearch />)
    await user.type(screen.getByLabelText("活動名稱、關鍵字或活動網址"), "   ")
    expect(screen.getByRole("button", { name: "搜尋" })).toBeDisabled()
    expect(api.searchEvents).not.toHaveBeenCalled()
  })

  it("直接載入帶 q 的網址就會執行搜尋並還原輸入框", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })

    renderWithProviders(<EventSearch />)

    await waitFor(() =>
      expect(api.searchEvents).toHaveBeenCalledWith("五月天", expect.anything())
    )
    expect(screen.getByLabelText("活動名稱、關鍵字或活動網址")).toHaveValue(
      "五月天"
    )
    expect(await screen.findByText("五月天 2026 諾亞方舟")).toBeInTheDocument()
  })

  it("清除搜尋會把 q 從網址移除", async () => {
    const user = userEvent.setup()
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })

    renderWithProviders(<EventSearch />)
    await user.click(await screen.findByRole("button", { name: "清除搜尋" }))

    expect(nav.push).toHaveBeenCalledWith("/")
  })

  it("搜尋頁沒有任何主辦代號輸入框", () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })
    renderWithProviders(<EventSearch />)

    expect(screen.queryByLabelText(/主辦代號/)).toBeNull()
    expect(screen.queryByText(/主辦代號/)).toBeNull()
    expect(screen.queryByText(/feed/i)).toBeNull()
    // 整頁只有一個輸入框：活動查詢。沒有任何內部搜尋參數欄位。
    expect(screen.getAllByRole("searchbox")).toHaveLength(1)
    expect(screen.queryAllByRole("textbox")).toHaveLength(0)
  })

  it("活動卡片顯示標題、描述、主辦單位與票券商，不顯示內部欄位", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })

    renderWithProviders(<EventSearch />)

    expect(await screen.findByText("五月天 2026 諾亞方舟")).toBeInTheDocument()
    expect(screen.getByText("五月天世界巡迴演唱會台北站")).toBeInTheDocument()
    expect(screen.getByText("相信音樂")).toBeInTheDocument()
    expect(screen.getByText("KKTIX")).toBeInTheDocument()
    expect(screen.getByText("尚未開賣")).toBeInTheDocument()

    expect(screen.queryByText(/score/i)).toBeNull()
    expect(screen.queryByText(/matched_by/)).toBeNull()
    expect(screen.queryByText(/raw_metadata/)).toBeNull()
    expect(screen.queryByText("ANNOUNCED")).toBeNull()
    expect(screen.queryByText(result().canonical_url)).toBeNull()
  })

  it("同一場活動有多個票券商時全部顯示", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({
      query: "五月天",
      results: [
        result({
          ticketing_providers: [
            { id: "kktix", name: "KKTIX", event_url: "https://a.test" },
            { id: "tixcraft", name: "拓元售票", event_url: "https://b.test" },
          ],
        }),
      ],
    })

    renderWithProviders(<EventSearch />)
    expect(await screen.findByText("KKTIX")).toBeInTheDocument()
    expect(screen.getByText("拓元售票")).toBeInTheDocument()
  })

  it("選擇活動的連結指向 /tasks/{eventId}/new", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })

    renderWithProviders(<EventSearch />)
    const link = await screen.findByRole("link", { name: "選擇此活動" })
    expect(link).toHaveAttribute("href", "/tasks/ev_mayday01/new")
  })

  it("多筆結果時不替使用者自動選擇", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({
      query: "五月天",
      results: [
        result(),
        result({ id: "ev_mayday02", title: "五月天 高雄場" }),
      ],
    })

    renderWithProviders(<EventSearch />)
    const links = await screen.findAllByRole("link", { name: "選擇此活動" })
    expect(links).toHaveLength(2)
    expect(nav.push).not.toHaveBeenCalled()
  })

  it("查無結果時顯示自然語言訊息", async () => {
    nav.params = new URLSearchParams("q=沒有這個活動")
    api.searchEvents.mockResolvedValue({ query: "沒有這個活動", results: [] })

    renderWithProviders(<EventSearch />)
    expect(await screen.findByText("查無符合的活動")).toBeInTheDocument()
  })

  it("上游票券平台失敗時顯示可讀訊息而不是後端英文字串", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockRejectedValue(
      new ApiError(
        502,
        "upstream_failed",
        "feed fetch failed: org=x status=500"
      )
    )

    renderWithProviders(<EventSearch />)
    expect(await screen.findByText("搜尋失敗")).toBeInTheDocument()
    expect(
      screen.getByText("票券平台暫時無法回應，請稍後再試。")
    ).toBeInTheDocument()
    expect(screen.queryByText(/feed fetch failed/)).toBeNull()
  })

  it("網路錯誤顯示連線提示", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockRejectedValue(new ApiError(0, "network_error", "boom"))

    renderWithProviders(<EventSearch />)
    expect(
      await screen.findByText("無法連線到伺服器，請確認網路或稍後再試。")
    ).toBeInTheDocument()
  })

  it("回傳資料不完整時仍然渲染，缺的欄位改顯示說明文字", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({
      query: "五月天",
      results: [
        result({
          description: null,
          organizer: null,
          ticketing_providers: [],
          sale_start_at: null,
          event_start_at: null,
          status: "WHAT_IS_THIS",
        }),
      ],
    })

    renderWithProviders(<EventSearch />)
    expect(await screen.findByText("此活動沒有提供描述。")).toBeInTheDocument()
    expect(screen.getByText("未提供主辦單位")).toBeInTheDocument()
    expect(screen.getByText("未提供票券商")).toBeInTheDocument()
    expect(screen.getByText("未知狀態")).toBeInTheDocument()
    expect(screen.queryByText("WHAT_IS_THIS")).toBeNull()
  })

  it("舊查詢的結果不會覆蓋比較新的查詢", async () => {
    const slow = result({ id: "ev_old", title: "舊查詢的活動" })
    const fast = result({ id: "ev_new", title: "新查詢的活動" })

    let resolveSlow: (value: unknown) => void = () => {}
    api.searchEvents.mockImplementation((query: string) => {
      if (query === "舊查詢") {
        return new Promise((resolve) => {
          resolveSlow = resolve
        })
      }
      return Promise.resolve({ query, results: [fast] })
    })

    nav.params = new URLSearchParams("q=舊查詢")
    const { rerender } = renderWithProviders(<EventSearch />)

    // 使用者在舊查詢還沒回來前就改了查詢條件。
    nav.params = new URLSearchParams("q=新查詢")
    rerender(<EventSearch />)
    expect(await screen.findByText("新查詢的活動")).toBeInTheDocument()

    // 舊請求這時才回來，畫面必須維持新查詢的結果。
    resolveSlow({ query: "舊查詢", results: [slow] })
    await waitFor(() =>
      expect(screen.getByText("新查詢的活動")).toBeInTheDocument()
    )
    expect(screen.queryByText("舊查詢的活動")).toBeNull()
  })

  it("輸入關鍵字後透過 debounce 自動觸發搜尋，不需點擊搜尋按鈕", async () => {
    api.searchEvents.mockResolvedValue({
      query: "告五人",
      results: [result({ id: "ev_accusefive", title: "告五人演唱會" })],
    })
    const user = userEvent.setup()
    renderWithProviders(<EventSearch />)

    await user.type(
      screen.getByLabelText("活動名稱、關鍵字或活動網址"),
      "告五人"
    )

    // 不點擊搜尋按鈕，等待 debounce（300ms）後自動觸發 searchEvents API
    await waitFor(
      () =>
        expect(api.searchEvents).toHaveBeenCalledWith(
          "告五人",
          expect.anything()
        ),
      { timeout: 1500 }
    )
    expect(await screen.findByText("告五人演唱會")).toBeInTheDocument()
  })

  it("搜尋框具備 70% 寬度且居中佈局，不再包裹於外層卡片 (Panel)", () => {
    const { container } = renderWithProviders(<EventSearch />)
    const searchbox = screen.getByRole("searchbox")
    const searchContainer = searchbox.closest(".md\\:w-\\[70\\%\\]")
    expect(searchContainer).not.toBeNull()
    expect(searchContainer).toHaveClass("mx-auto")

    // 不存在多餘的外層 Panel / Card
    expect(container.querySelector("[data-slot='card']")).toBeNull()
  })
})
