import * as React from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "./helpers/render"

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  params: new URLSearchParams(),
}))

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  useSearchParams: () => nav.params,
}))

const api = vi.hoisted(() => ({
  searchEvents: vi.fn(),
  getEventStatuses: vi.fn(),
}))
vi.mock("@/lib/api/events", () => ({
  searchEvents: api.searchEvents,
  getEventStatuses: api.getEventStatuses,
}))

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
    detail_loaded: true,
    ...overrides,
  }
}

beforeEach(() => {
  nav.push.mockClear()
  nav.replace.mockClear()
  nav.params = new URLSearchParams()
  api.searchEvents.mockReset()
  api.getEventStatuses.mockReset()
  // 票況是搜尋之後才非同步補的；預設當成還沒補到，卡片顯示搜尋回來的狀態。
  api.getEventStatuses.mockResolvedValue({ results: [] })
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
    await user.click(screen.getByRole("button", { name: "搜尋" }))

    // 等過 debounce 視窗，確認全程都沒有打出去。
    await new Promise((resolve) => setTimeout(resolve, 500))
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

  it("清空搜尋文字會把 q 從網址移除", async () => {
    const user = userEvent.setup()
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })

    renderWithProviders(<EventSearch />)
    const input = screen.getByLabelText("活動名稱、關鍵字或活動網址")
    await user.clear(input)

    // 打字中的同步走 replace，不在瀏覽歷史留下每一個中間字串。
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/"), {
      timeout: 1500,
    })
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

  it("活動卡片顯示標題、描述、主辦單位與平台，不顯示內部欄位", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [result()] })

    renderWithProviders(<EventSearch />)

    const cardTitle = await screen.findByText("五月天 2026 諾亞方舟")
    const card = cardTitle.closest("li")
    expect(card).not.toBeNull()
    expect(card).toHaveTextContent("KKTIX")
    expect(card).toHaveTextContent("五月天世界巡迴演唱會台北站")
    expect(card).toHaveTextContent("相信音樂")
    expect(card).toHaveTextContent("尚未開賣")

    expect(screen.queryByText(/score/i)).toBeNull()
    expect(screen.queryByText(/matched_by/)).toBeNull()
    expect(screen.queryByText(/raw_metadata/)).toBeNull()
    expect(screen.queryByText("ANNOUNCED")).toBeNull()
    expect(screen.queryByText(result().canonical_url)).toBeNull()
  })

  it("票況還沒確認完時放骨架動畫，不放代表票況的文字", async () => {
    nav.params = new URLSearchParams("q=五月天")
    api.searchEvents.mockResolvedValue({
      query: "五月天",
      results: [result({ status: "UNKNOWN", detail_loaded: false })],
    })

    renderWithProviders(<EventSearch />)
    const card = (await screen.findByText("五月天 2026 諾亞方舟")).closest("li")
    expect(card).not.toBeNull()

    const statusCell = card!.querySelector("dd[aria-busy='true']")
    expect(statusCell).not.toBeNull()
    expect(statusCell!.querySelector("[data-slot='skeleton']")).not.toBeNull()

    // 骨架期間不得出現任何看得到的票況字樣——那會被當成一種票況。
    expect(card).not.toHaveTextContent("狀態未確認")
    expect(card).not.toHaveTextContent("熱賣中")
    expect(card).not.toHaveTextContent("尚未開賣")
    expect(card).not.toHaveTextContent("已售罄")
    // 讀螢幕的人還是要知道這一格在等資料。
    expect(statusCell!.querySelector(".sr-only")).toHaveTextContent(
      "票況確認中…"
    )
  })

  it("票況確認完之後骨架換成真正的狀態文字", async () => {
    nav.params = new URLSearchParams("q=五月天")
    const shallow = result({ status: "UNKNOWN", detail_loaded: false })
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [shallow] })
    api.getEventStatuses.mockResolvedValue({
      results: [
        {
          id: shallow.id,
          status: "ANNOUNCED",
          sale_start_at: shallow.sale_start_at,
          sale_end_at: null,
          event_start_at: shallow.event_start_at,
          detail_loaded: true,
          checked_at: "2026-09-22T03:00:00Z",
        },
      ],
    })

    renderWithProviders(<EventSearch />)
    const card = (await screen.findByText("五月天 2026 諾亞方舟")).closest("li")
    await waitFor(() => expect(card).toHaveTextContent("尚未開賣"))
    expect(card!.querySelector("[data-slot='skeleton']")).toBeNull()
    expect(card!.querySelector("dd[aria-busy='true']")).toBeNull()
  })

  it("票況確認完之後卡片就地換成真正的狀態", async () => {
    nav.params = new URLSearchParams("q=五月天")
    const shallow = result({ status: "UNKNOWN", detail_loaded: false })
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [shallow] })
    api.getEventStatuses.mockResolvedValue({
      results: [
        {
          id: shallow.id,
          status: "SOLD_OUT",
          sale_start_at: shallow.sale_start_at,
          sale_end_at: null,
          event_start_at: shallow.event_start_at,
          detail_loaded: true,
          checked_at: "2026-09-22T03:00:00Z",
        },
      ],
    })

    renderWithProviders(<EventSearch />)
    const card = (await screen.findByText("五月天 2026 諾亞方舟")).closest("li")
    await waitFor(() => expect(card).toHaveTextContent("已售罄"))
    // 一次問一整批，不是每張卡片各打一次。
    expect(api.getEventStatuses).toHaveBeenCalledWith(
      [shallow.id],
      expect.anything()
    )
  })

  it("票況說已結束的活動會從結果裡收起來", async () => {
    nav.params = new URLSearchParams("q=五月天")
    const shallow = result({ status: "UNKNOWN", detail_loaded: false })
    api.searchEvents.mockResolvedValue({ query: "五月天", results: [shallow] })
    api.getEventStatuses.mockResolvedValue({
      results: [
        {
          id: shallow.id,
          status: "CLOSED",
          sale_start_at: null,
          sale_end_at: null,
          event_start_at: null,
          detail_loaded: true,
          checked_at: "2026-09-22T03:00:00Z",
        },
      ],
    })

    renderWithProviders(<EventSearch />)
    await waitFor(() =>
      expect(screen.queryByText("五月天 2026 諾亞方舟")).toBeNull()
    )
  })

  it("同一場活動有多個平台時全部顯示", async () => {
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
    expect(screen.getByText("未提供平台")).toBeInTheDocument()
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

  it("不顯示已結束的活動，且不顯示活動狀態 Tabs", async () => {
    nav.params = new URLSearchParams("q=音樂會")
    const onSale = result({
      id: "ev_1",
      title: "開賣中的音樂會",
      status: "ON_SALE",
    })
    const announced = result({
      id: "ev_2",
      title: "尚未開賣的音樂會",
      status: "ANNOUNCED",
    })
    const soldOut = result({
      id: "ev_3",
      title: "已售完的音樂會",
      status: "SOLD_OUT",
    })
    const closed = result({
      id: "ev_4",
      title: "已結束的音樂會",
      status: "CLOSED",
    })

    api.searchEvents.mockResolvedValue({
      query: "音樂會",
      results: [onSale, announced, soldOut, closed],
    })

    renderWithProviders(<EventSearch />)

    // 顯示未結束活動
    expect(await screen.findByText("開賣中的音樂會")).toBeInTheDocument()
    expect(screen.getByText("尚未開賣的音樂會")).toBeInTheDocument()
    expect(screen.getByText("已售完的音樂會")).toBeInTheDocument()

    // 不顯示已結束活動
    expect(screen.queryByText("已結束的音樂會")).toBeNull()

    // 不存在狀態 Tabs
    expect(screen.queryByRole("tablist")).toBeNull()
    expect(screen.queryByRole("tab")).toBeNull()
  })

  it("搜尋框下方具備日期範圍選擇器與活動狀態、平台 Select 下拉選單", async () => {
    nav.params = new URLSearchParams("q=音樂會")
    api.searchEvents.mockResolvedValue({
      query: "音樂會",
      results: [result()],
    })

    renderWithProviders(<EventSearch />)

    // 驗證日期範圍選擇按鈕存在
    expect(
      screen.getByRole("button", { name: /選擇日期範圍/ })
    ).toBeInTheDocument()

    // 驗證活動狀態 Select 存在
    const statusSelect = screen.getByRole("combobox", { name: "活動狀態" })
    expect(statusSelect).toBeInTheDocument()
    expect(statusSelect).toHaveTextContent("全部狀態")

    // 驗證平台 Select 存在
    const providerSelect = screen.getByRole("combobox", { name: "平台" })
    expect(providerSelect).toBeInTheDocument()
    expect(providerSelect).toHaveTextContent("全部平台")
  })

  it("可透過活動狀態 Select 進行篩選", async () => {
    nav.params = new URLSearchParams("q=音樂會")
    const onSale = result({
      id: "ev_1",
      title: "開賣中的音樂會",
      status: "ON_SALE",
    })
    const announced = result({
      id: "ev_2",
      title: "尚未開賣的音樂會",
      status: "ANNOUNCED",
    })

    api.searchEvents.mockResolvedValue({
      query: "音樂會",
      results: [onSale, announced],
    })

    renderWithProviders(<EventSearch />)

    expect(await screen.findByText("開賣中的音樂會")).toBeInTheDocument()
    expect(screen.getByText("尚未開賣的音樂會")).toBeInTheDocument()

    // 透過活動狀態 Select 的 native select 切換為「ON_SALE」
    const statusSelectEl = document.querySelector('select[name="status"]')
    expect(statusSelectEl).not.toBeNull()
    fireEvent.change(statusSelectEl!, { target: { value: "ON_SALE" } })

    // 僅顯示開賣中的活動
    expect(screen.getByText("開賣中的音樂會")).toBeInTheDocument()
    expect(screen.queryByText("尚未開賣的音樂會")).toBeNull()
  })

  it("可透過平台 Select 進行篩選", async () => {
    nav.params = new URLSearchParams("q=音樂會")
    const kktixEvent = result({
      id: "ev_1",
      title: "KKTIX 的音樂會",
      ticketing_providers: [
        { id: "kktix", name: "KKTIX", event_url: "https://a.test" },
      ],
    })
    const tixcraftEvent = result({
      id: "ev_2",
      title: "拓元的音樂會",
      ticketing_providers: [
        { id: "tixcraft", name: "拓元售票", event_url: "https://b.test" },
      ],
    })

    api.searchEvents.mockResolvedValue({
      query: "音樂會",
      results: [kktixEvent, tixcraftEvent],
    })

    renderWithProviders(<EventSearch />)

    expect(await screen.findByText("KKTIX 的音樂會")).toBeInTheDocument()
    expect(screen.getByText("拓元的音樂會")).toBeInTheDocument()

    // 透過平台 Select 的 native select 切換為「tixcraft」
    const providerSelectEl = document.querySelector('select[name="provider"]')
    expect(providerSelectEl).not.toBeNull()
    fireEvent.change(providerSelectEl!, { target: { value: "tixcraft" } })

    // 僅顯示拓元的活動
    await waitFor(() => {
      expect(screen.getByText("拓元的音樂會")).toBeInTheDocument()
      expect(screen.queryByText("KKTIX 的音樂會")).toBeNull()
    })
  })

  it("當篩選後無符合活動時顯示自訂空狀態", async () => {
    nav.params = new URLSearchParams("q=音樂會")
    const onSale = result({
      id: "ev_1",
      title: "開賣中的音樂會",
      status: "ON_SALE",
    })

    api.searchEvents.mockResolvedValue({
      query: "音樂會",
      results: [onSale],
    })

    renderWithProviders(<EventSearch />)

    expect(await screen.findByText("開賣中的音樂會")).toBeInTheDocument()

    // 切換為「已售罄」
    const statusSelectEl = document.querySelector('select[name="status"]')
    expect(statusSelectEl).not.toBeNull()
    fireEvent.change(statusSelectEl!, { target: { value: "SOLD_OUT" } })

    expect(
      await screen.findByText("沒有符合篩選條件的活動")
    ).toBeInTheDocument()
  })

  it("可透過日期範圍選擇器篩選活動", async () => {
    const user = userEvent.setup()
    nav.params = new URLSearchParams("q=音樂會")
    const decEvent = result({
      id: "ev_dec",
      title: "十二月音樂會",
      event_start_at: "2026-12-25T11:00:00Z",
    })
    const janEvent = result({
      id: "ev_jan",
      title: "一月音樂會",
      event_start_at: "2026-01-10T11:00:00Z",
    })

    api.searchEvents.mockResolvedValue({
      query: "音樂會",
      results: [decEvent, janEvent],
    })

    renderWithProviders(<EventSearch />)

    expect(await screen.findByText("十二月音樂會")).toBeInTheDocument()
    expect(screen.getByText("一月音樂會")).toBeInTheDocument()

    // 點擊日期範圍按鈕打開 Popover
    const dateRangeBtn = screen.getByRole("button", { name: /選擇日期範圍/ })
    await user.click(dateRangeBtn)

    // 驗證 Calendar 已打開（雙月份模式顯示 2 個月曆網格）
    expect(screen.getAllByRole("grid")).toHaveLength(2)
  })
})
