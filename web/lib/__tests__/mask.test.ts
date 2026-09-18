import { describe, expect, it } from "vitest"

import { maskEmail, maskIdNumber, maskPhone, maskSecret } from "@/lib/mask"

describe("maskIdNumber", () => {
  it("保留首 1 碼與末 3 碼，中間固定 4 個星號", () => {
    expect(maskIdNumber("A123456789")).toBe("A****789")
  })

  it("空值回空字串", () => {
    expect(maskIdNumber("")).toBe("")
    expect(maskIdNumber(null)).toBe("")
    expect(maskIdNumber(undefined)).toBe("")
  })

  it("短於保留長度時全遮，不外洩任何字元", () => {
    expect(maskIdNumber("A1")).toBe("**")
    expect(maskIdNumber("A123")).toBe("****")
  })

  it("中段星號數固定，不洩漏原始長度", () => {
    expect(maskIdNumber("A123456789012345")).toBe("A****345")
  })
})

describe("maskPhone", () => {
  it("只保留末 3 碼", () => {
    expect(maskPhone("0912345678")).toBe("*******678")
  })

  it("空值與過短輸入", () => {
    expect(maskPhone("")).toBe("")
    expect(maskPhone("12")).toBe("**")
    expect(maskPhone("123")).toBe("***")
  })
})

describe("maskEmail", () => {
  it("local part 只保留首 1 碼", () => {
    expect(maskEmail("alice@x.com")).toBe("a****@x.com")
  })

  it("沒有 @ 時退回一般遮罩，不原樣回傳", () => {
    expect(maskEmail("notanemail")).toBe("n****ail")
  })

  it("local part 為空", () => {
    expect(maskEmail("@x.com")).toBe("****@x.com")
  })

  it("空值", () => {
    expect(maskEmail("")).toBe("")
    expect(maskEmail(null)).toBe("")
  })
})

describe("maskSecret", () => {
  it("恆回固定星號，完全不看輸入", () => {
    expect(maskSecret()).toBe("********")
  })
})
