import { describe, expect, it } from "vitest"

import {
  LEVEL_WIDTH,
  appendEntry,
  deriveLevel,
  entriesToText,
  entryKey,
  filterEntries,
  levelLabel,
  summarize,
  toEntry,
  type LogEntry,
  type LogLevel,
} from "@/lib/log-buffer"
import type { ServerMessage } from "@/lib/ws/types"

const TS = "2026-09-18T10:00:00+00:00"

function stateChanged(outboxId: number | null): ServerMessage {
  return {
    type: "STATE_CHANGED",
    task_id: "t1",
    timestamp: TS,
    outbox_id: outboxId,
    payload: {
      from_state: "IDLE",
      to_state: "PREPARING",
      event: "prepare_session",
      elapsed_ms: 12.5,
    },
  }
}

function taskLog(eventType: string): ServerMessage {
  return {
    type: "TASK_LOG",
    task_id: "t1",
    timestamp: TS,
    outbox_id: null,
    payload: { event_type: eventType, name: "step", detail: "ok" },
  }
}

describe("deriveLevel", () => {
  it("依訊息型別推導等級", () => {
    expect(deriveLevel(stateChanged(1))).toBe("state")
    expect(
      deriveLevel({
        type: "CLOCK_TICK",
        task_id: "t1",
        timestamp: TS,
        payload: { server_time: TS, time_to_sale_ms: 1000, clock_offset_ms: 3 },
      })
    ).toBe("tick")
    expect(
      deriveLevel({
        type: "SCREENSHOT_CAPTURED",
        task_id: "t1",
        timestamp: TS,
        payload: {
          state: "SALE_OPEN",
          sequence: 1,
          url: "/static/screenshots/a.png",
        },
      })
    ).toBe("shot")
    expect(
      deriveLevel({
        type: "ERROR",
        task_id: "t1",
        timestamp: TS,
        payload: { name: "n", error_type: "E", error_message: "boom" },
      })
    ).toBe("error")
  })

  it("TASK_LOG 的 snapshot 標為 snap", () => {
    expect(
      deriveLevel({
        type: "TASK_LOG",
        task_id: "t1",
        timestamp: TS,
        payload: {
          phase: "snapshot",
          task_status: "RUNNING",
          job_state: "RUNNING",
          experiment_id: null,
        },
      })
    ).toBe("snap")
  })

  it("TASK_LOG 的 event_type=error 升級為 error", () => {
    expect(deriveLevel(taskLog("error"))).toBe("error")
    expect(deriveLevel(taskLog("info"))).toBe("info")
  })
})

describe("levelLabel", () => {
  it("固定 5 字元寬，維持等寬對齊", () => {
    for (const lv of [
      "error",
      "state",
      "shot",
      "tick",
      "info",
      "snap",
    ] as LogLevel[]) {
      expect(levelLabel(lv)).toHaveLength(LEVEL_WIDTH)
    }
    expect(levelLabel("info")).toBe("INFO ")
  })
})

describe("entryKey 去重鍵", () => {
  it("有 outbox_id 時以其為鍵", () => {
    expect(entryKey(stateChanged(42), 0)).toBe("o42")
  })

  it("outbox_id 為 null 時退回 timestamp|type|seq，不參與跨連線去重", () => {
    expect(entryKey(stateChanged(null), 7)).toBe(`${TS}|STATE_CHANGED|7`)
  })
})

describe("appendEntry 環形緩衝", () => {
  it("重連回放同一 outbox_id 不會產生重複列", () => {
    const seen = new Set<string>()
    let buf: LogEntry[] = []
    buf = appendEntry(buf, toEntry(stateChanged(1), 0), seen)
    buf = appendEntry(buf, toEntry(stateChanged(1), 1), seen)
    expect(buf).toHaveLength(1)
  })

  it("未變更時回傳同一個陣列參考（讓 React 可跳過重繪）", () => {
    const seen = new Set<string>()
    const first = appendEntry([], toEntry(stateChanged(1), 0), seen)
    const second = appendEntry(first, toEntry(stateChanged(1), 1), seen)
    expect(second).toBe(first)
  })

  it("超過上限丟最舊，長度不超過 limit", () => {
    const seen = new Set<string>()
    let buf: LogEntry[] = []
    for (let i = 0; i < 10; i++) {
      buf = appendEntry(buf, toEntry(stateChanged(i), i), seen, 4)
    }
    expect(buf).toHaveLength(4)
    expect(buf.map((e) => e.key)).toEqual(["o6", "o7", "o8", "o9"])
  })

  it("outbox_id 為 null 的訊息（snapshot / ACK）各自獨立保留", () => {
    const seen = new Set<string>()
    let buf: LogEntry[] = []
    buf = appendEntry(buf, toEntry(taskLog("info"), 0), seen)
    buf = appendEntry(buf, toEntry(taskLog("info"), 1), seen)
    expect(buf).toHaveLength(2)
  })
})

describe("summarize", () => {
  it("STATE_CHANGED 顯示轉移與耗時", () => {
    expect(summarize(stateChanged(1))).toBe(
      "IDLE → PREPARING  (prepare_session, 13ms)"
    )
  })

  it("協定錯誤顯示 reason", () => {
    expect(
      summarize({
        type: "ERROR",
        task_id: "t1",
        timestamp: TS,
        payload: { reason: "missing_target_state" },
      })
    ).toBe("missing_target_state")
  })

  it("指令 ACK 顯示 action 與 accepted", () => {
    expect(
      summarize({
        type: "TASK_LOG",
        task_id: "t1",
        timestamp: TS,
        payload: { action: "PAUSE", accepted: true, signal_id: 9 },
      })
    ).toBe("PAUSE accepted=true signal=9")
  })
})

describe("filterEntries", () => {
  const all: LogLevel[] = ["error", "state", "shot", "tick", "info", "snap"]
  const entries = [
    toEntry(stateChanged(1), 0),
    toEntry(taskLog("info"), 1),
    toEntry(taskLog("error"), 2),
  ]

  it("等級過濾", () => {
    expect(filterEntries(entries, new Set(["state"]), "")).toHaveLength(1)
    expect(filterEntries(entries, new Set(all), "")).toHaveLength(3)
  })

  it("搜尋以子字串比對摘要，且不分大小寫", () => {
    expect(filterEntries(entries, new Set(all), "PREPARING")).toHaveLength(1)
    expect(filterEntries(entries, new Set(all), "preparing")).toHaveLength(1)
    expect(filterEntries(entries, new Set(all), "查無此物")).toHaveLength(0)
  })
})

describe("entriesToText", () => {
  it("輸出純文字，欄位以等寬對齊", () => {
    const text = entriesToText([toEntry(stateChanged(1), 0)])
    expect(text).toContain("STATE")
    expect(text).toContain("IDLE → PREPARING")
    expect(text.split("\n")).toHaveLength(1)
  })
})
