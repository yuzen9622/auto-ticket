import { describe, expect, it } from "vitest"

import {
  CLIENT_ACTION,
  JOB_STATE,
  PURCHASE_STATE,
  SEAT_STRATEGY,
  TASK_STATUS,
} from "@/lib/contract"
import { messages } from "@/lib/i18n/config"

/**
 * 契約裡的每一個 enum 值都必須有翻譯——漏一個，使用者就會看到 `WAITING_FOR_SALE`。
 */
describe("狀態字典涵蓋全部後端 enum", () => {
  const cases: [string, readonly string[], Record<string, string>][] = [
    ["TaskStatus", TASK_STATUS, messages.status.taskStatus],
    ["PurchaseState", PURCHASE_STATE, messages.status.purchaseState],
    ["JobState", JOB_STATE, messages.status.jobState],
    ["ClientAction", CLIENT_ACTION, messages.status.clientAction],
    ["SeatStrategy", SEAT_STRATEGY, messages.status.seatStrategy],
  ]

  for (const [name, values, dictionary] of cases) {
    it(`${name} 每個值都有中文標籤`, () => {
      for (const value of values) {
        expect(dictionary[value], `${name}.${value}`).toBeTruthy()
      }
    })
  }

  it("規格點名的狀態翻譯逐字符合", () => {
    expect(messages.status.taskStatus.SCHEDULED).toBe("已排程")
    expect(messages.status.taskStatus.RUNNING).toBe("執行中")
    expect(messages.status.taskStatus.COMPLETED).toBe("已完成")
    expect(messages.status.taskStatus.FAILED).toBe("失敗")
    expect(messages.status.taskStatus.CANCELLED).toBe("已取消")
    expect(messages.status.purchaseState.PREPARING).toBe("準備中")
    expect(messages.status.purchaseState.WAITING_FOR_SALE).toBe("等待開賣")
    expect(messages.status.purchaseState.SALE_OPEN).toBe("已開賣")
    expect(messages.status.purchaseState.TICKET_SELECTION).toBe("選擇票種")
    expect(messages.status.purchaseState.SEAT_SELECTION).toBe("選擇座位")
    expect(messages.status.purchaseState.FORM_FILLING).toBe("填寫資料")
    expect(messages.status.purchaseState.VERIFICATION_REQUIRED).toBe("需要驗證")
    expect(messages.status.purchaseState.PAYMENT_REQUIRED).toBe("等待付款")
    expect(messages.status.purchaseState.PAYMENT_PROCESSING).toBe("付款處理中")
    expect(messages.status.purchaseState.SOLD_OUT).toBe("已售罄")
    expect(messages.status.purchaseState.TIMEOUT).toBe("已逾時")
    expect(messages.status.unknown).toBe("未知狀態")
  })

  it("座位策略顯示自然語言，不夾帶原始值", () => {
    expect(messages.status.seatStrategy.best_available).toBe("最佳可選")
    expect(messages.status.seatStrategy.same_zone).toBe("同一區域")
    expect(messages.status.seatStrategy.specific_zone).toBe("指定區域")
  })

  it("執行模式有自然語言標籤", () => {
    expect(messages.status.executionMode.live).toBe("正式模式")
    expect(messages.status.executionMode.mock).toBe("測試模式")
  })
})
