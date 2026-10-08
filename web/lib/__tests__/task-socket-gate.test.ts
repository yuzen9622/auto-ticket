import { act, renderHook } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { useTaskSocket } from "@/lib/ws/use-task-socket"

class Socket {
  static current: Socket
  onmessage: ((event: { data: string }) => void) | null = null
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  close() {}
  constructor() {
    Socket.current = this
  }
  message(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify({ type: "TASK_LOG", payload }) })
  }
}

afterEach(() => vi.unstubAllGlobals())

it("重連清除舊提示，回放恢復提示，人工完成後清除", () => {
  vi.stubGlobal("WebSocket", Socket)
  const { result, unmount } = renderHook(() => useTaskSocket("qa"))
  const gate = {
    phase: "waiting_for_human",
    page_kind: "SEAT_SELECTION",
    attempt: 1,
    hint: "請選位",
  }
  act(() => Socket.current.message(gate))
  expect(result.current.humanGate?.hint).toBe("請選位")
  act(() =>
    Socket.current.message({ phase: "snapshot", task_status: "RUNNING" })
  )
  expect(result.current.humanGate).toBeNull()
  act(() => Socket.current.message(gate))
  expect(result.current.humanGate?.page_kind).toBe("SEAT_SELECTION")
  act(() => Socket.current.message({ phase: "human_gate_resolved" }))
  expect(result.current.humanGate).toBeNull()
  unmount()
})
