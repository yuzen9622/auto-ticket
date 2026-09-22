import * as React from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"

const api = vi.hoisted(() => ({ getEventStatuses: vi.fn() }))
vi.mock("@/lib/api/events", () => ({ getEventStatuses: api.getEventStatuses }))

import { useEventStatuses } from "@/hooks/use-event-statuses"

function row(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    status: "ON_SALE",
    sale_start_at: null,
    sale_end_at: null,
    event_start_at: null,
    detail_loaded: true,
    checked_at: "2026-09-22T03:00:00Z",
    ...overrides,
  }
}

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  api.getEventStatuses.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("useEventStatuses", () => {
  it("一次問一整批 id，不是每個活動各發一次請求", async () => {
    api.getEventStatuses.mockResolvedValue({ results: [row("a"), row("b")] })

    const { result } = renderHook(() => useEventStatuses(["a", "b"]), {
      wrapper,
    })

    await waitFor(() => expect(result.current.statuses.size).toBe(2))
    expect(api.getEventStatuses).toHaveBeenCalledTimes(1)
    expect(api.getEventStatuses).toHaveBeenCalledWith(
      ["a", "b"],
      expect.anything()
    )
  })

  it("全部票況都確認過就停止輪詢", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.getEventStatuses.mockResolvedValue({ results: [row("a")] })

    renderHook(() => useEventStatuses(["a"]), { wrapper })

    await waitFor(() => expect(api.getEventStatuses).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(20_000)
    // 停不下來的話，二十秒會再打十次。
    expect(api.getEventStatuses).toHaveBeenCalledTimes(1)
  })

  it("還沒確認完就繼續問，但問到上限就收手", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    // 後端一直回「還沒確認」——沒有 Worker 在跑時就是這個樣子。
    api.getEventStatuses.mockResolvedValue({
      results: [row("a", { checked_at: null, status: "UNKNOWN" })],
    })

    renderHook(() => useEventStatuses(["a"]), { wrapper })

    await waitFor(() => expect(api.getEventStatuses).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(10_000)
    const midway = api.getEventStatuses.mock.calls.length
    expect(midway).toBeGreaterThan(1)

    await vi.advanceTimersByTimeAsync(120_000)
    const settled = api.getEventStatuses.mock.calls.length
    expect(settled).toBeLessThanOrEqual(15)

    await vi.advanceTimersByTimeAsync(120_000)
    expect(api.getEventStatuses.mock.calls.length).toBe(settled)
  })

  it("換一組活動時輪詢次數重新計算", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.getEventStatuses.mockResolvedValue({
      results: [row("a", { checked_at: null })],
    })

    const { rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useEventStatuses(ids),
      { wrapper, initialProps: { ids: ["a"] } }
    )

    // 先把第一組問到上限。
    await vi.advanceTimersByTimeAsync(120_000)
    const exhausted = api.getEventStatuses.mock.calls.length
    expect(exhausted).toBeGreaterThan(1)

    api.getEventStatuses.mockResolvedValue({
      results: [row("b", { checked_at: null })],
    })
    rerender({ ids: ["b"] })

    // 沿用上一組的計數的話，新的一組會一次都問不到。
    await waitFor(() =>
      expect(
        api.getEventStatuses.mock.calls.some(
          (call: unknown[]) => JSON.stringify(call[0]) === JSON.stringify(["b"])
        )
      ).toBe(true)
    )
    await vi.advanceTimersByTimeAsync(10_000)
    const forB = api.getEventStatuses.mock.calls.filter(
      (call: unknown[]) => JSON.stringify(call[0]) === JSON.stringify(["b"])
    ).length
    expect(forB).toBeGreaterThan(1)
  })

  it("沒有活動時不發請求", () => {
    const { result } = renderHook(() => useEventStatuses([]), { wrapper })
    expect(api.getEventStatuses).not.toHaveBeenCalled()
    expect(result.current.isSettled).toBe(true)
  })

  it("票況全部確認完就回報結束，卡片才收得掉骨架", async () => {
    api.getEventStatuses.mockResolvedValue({ results: [row("a")] })

    const { result } = renderHook(() => useEventStatuses(["a"]), { wrapper })

    expect(result.current.isSettled).toBe(false)
    await waitFor(() => expect(result.current.isSettled).toBe(true))
  })

  it("問到上限仍等不到確認時也回報結束——沒有 Worker 在跑就是這個情況", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.getEventStatuses.mockResolvedValue({
      results: [row("a", { checked_at: null, status: "UNKNOWN" })],
    })

    const { result } = renderHook(() => useEventStatuses(["a"]), { wrapper })

    await waitFor(() => expect(api.getEventStatuses).toHaveBeenCalledTimes(1))
    expect(result.current.isSettled).toBe(false)

    await vi.advanceTimersByTimeAsync(120_000)
    await waitFor(() => expect(result.current.isSettled).toBe(true))
  })
})
