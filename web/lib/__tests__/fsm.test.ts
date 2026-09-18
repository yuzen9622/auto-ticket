import { describe, expect, it } from "vitest"

import { PURCHASE_STATE, TASK_STATUS } from "@/lib/contract"
import {
  isFinalState,
  isTaskFinished,
  purchaseStateIndex,
  purchaseStateTone,
  taskStatusTone,
} from "@/lib/fsm"
import { derivePageState } from "@/lib/task-status"

describe("isFinalState", () => {
  it("四個終態為真", () => {
    for (const s of ["COMPLETED", "SOLD_OUT", "TIMEOUT", "FAILED"]) {
      expect(isFinalState(s)).toBe(true)
    }
  })

  it("非終態為假", () => {
    for (const s of [
      "IDLE",
      "PREPARING",
      "WAITING_FOR_SALE",
      "PAYMENT_PROCESSING",
    ]) {
      expect(isFinalState(s)).toBe(false)
    }
  })

  it("未知字串為假，不拋錯", () => {
    expect(isFinalState("NOT_A_STATE")).toBe(false)
  })
})

describe("isTaskFinished", () => {
  it("僅 COMPLETED / FAILED / CANCELLED 為真", () => {
    expect(isTaskFinished("COMPLETED")).toBe(true)
    expect(isTaskFinished("FAILED")).toBe(true)
    expect(isTaskFinished("CANCELLED")).toBe(true)
    expect(isTaskFinished("RUNNING")).toBe(false)
    expect(isTaskFinished("PAUSED")).toBe(false)
  })
})

describe("色票涵蓋全部 enum 值", () => {
  it("每個 PurchaseState 都有語意色", () => {
    for (const s of PURCHASE_STATE) {
      expect(["muted", "accent", "success", "warning", "danger"]).toContain(
        purchaseStateTone(s)
      )
    }
  })

  it("每個 TaskStatus 都有語意色", () => {
    for (const s of TASK_STATUS) {
      expect(["muted", "accent", "success", "warning", "danger"]).toContain(
        taskStatusTone(s)
      )
    }
  })

  it("未知值退回 muted，不拋錯", () => {
    expect(purchaseStateTone("???")).toBe("muted")
    expect(taskStatusTone("???")).toBe("muted")
  })
})

describe("purchaseStateIndex", () => {
  it("沿用後端宣告順序", () => {
    expect(purchaseStateIndex("IDLE")).toBe(0)
    expect(purchaseStateIndex("FAILED")).toBe(PURCHASE_STATE.length - 1)
  })

  it("未知狀態回 -1", () => {
    expect(purchaseStateIndex("???")).toBe(-1)
  })
})

describe("derivePageState 投影", () => {
  it("RUNNING 且 WS open 才是 LIVE", () => {
    expect(derivePageState("RUNNING", "open")).toBe("LIVE")
    expect(derivePageState("RUNNING", "reconnecting")).toBe("UNKNOWN")
  })

  it("CREATED / SCHEDULED → SCHEDULED", () => {
    expect(derivePageState("CREATED", "open")).toBe("SCHEDULED")
    expect(derivePageState("SCHEDULED", "closed")).toBe("SCHEDULED")
  })

  it("PREPARING / READY → WARMING_UP", () => {
    expect(derivePageState("PREPARING", "open")).toBe("WARMING_UP")
    expect(derivePageState("READY", "open")).toBe("WARMING_UP")
  })

  it("PAUSED → PAUSED", () => {
    expect(derivePageState("PAUSED", "open")).toBe("PAUSED")
  })

  it("三個終態 → FINISHED", () => {
    expect(derivePageState("COMPLETED", "closed")).toBe("FINISHED")
    expect(derivePageState("FAILED", "closed")).toBe("FINISHED")
    expect(derivePageState("CANCELLED", "closed")).toBe("FINISHED")
  })

  it("null / 未知 → UNKNOWN", () => {
    expect(derivePageState(null, "open")).toBe("UNKNOWN")
    expect(derivePageState("???", "open")).toBe("UNKNOWN")
  })
})
