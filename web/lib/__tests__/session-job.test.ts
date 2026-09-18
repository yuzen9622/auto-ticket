import { describe, expect, it } from "vitest"

import {
  JOB_PROGRESS_STEPS,
  explainJobError,
  formatElapsed,
  jobKindLabel,
  jobOutcomeTone,
  jobStateHint,
  jobStateLabel,
  jobStepCount,
  summarizeJobResult,
} from "@/lib/session-job"

describe("jobStateLabel / jobStateHint", () => {
  it("translates every broker job state", () => {
    for (const state of [
      "PENDING",
      "CLAIMED",
      "RUNNING",
      "DONE",
      "FAILED",
      "CANCELLED",
    ]) {
      expect(jobStateLabel(state)).not.toBe(state)
      expect(jobStateHint(state)).not.toBe("狀態未知。")
    }
  })

  it("falls back to the raw state when unknown", () => {
    expect(jobStateLabel("WAT")).toBe("WAT")
    expect(jobStateHint("WAT")).toBe("狀態未知。")
  })
})

describe("jobKindLabel", () => {
  it("translates the three session job kinds", () => {
    expect(jobKindLabel("SESSION_CHECK")).toBe("Session 檢查")
    expect(jobKindLabel("AUTO_LOGIN")).toBe("自動登入")
    expect(jobKindLabel("MANUAL_LOGIN")).toBe("人工登入")
  })

  it("falls back to the raw kind", () => {
    expect(jobKindLabel("PURCHASE")).toBe("PURCHASE")
  })
})

describe("jobStepCount", () => {
  it("advances monotonically through the live steps", () => {
    expect(jobStepCount("PENDING")).toBe(1)
    expect(jobStepCount("CLAIMED")).toBe(2)
    expect(jobStepCount("RUNNING")).toBe(3)
  })

  it("reports a full bar for every terminal state", () => {
    for (const state of ["DONE", "FAILED", "CANCELLED"]) {
      expect(jobStepCount(state)).toBe(JOB_PROGRESS_STEPS.length)
    }
  })
})

describe("jobOutcomeTone", () => {
  it("maps terminal states to their semantic tone", () => {
    expect(jobOutcomeTone("DONE")).toBe("success")
    expect(jobOutcomeTone("FAILED")).toBe("danger")
    expect(jobOutcomeTone("CANCELLED")).toBe("muted")
    expect(jobOutcomeTone("RUNNING")).toBe("accent")
  })
})

describe("explainJobError", () => {
  it("turns a missing browser binary into an install instruction", () => {
    const raw =
      "BrowserType.launch_persistent_context: Executable doesn't exist at /Users/x/ms-playwright/chromium/headless_shell"
    const out = explainJobError(raw)
    expect(out.title).toBe("Playwright 瀏覽器尚未安裝")
    expect(out.hint).toContain("playwright install chromium")
  })

  it("matches the playwright install hint wording too", () => {
    expect(explainJobError("run `playwright install`").title).toBe(
      "Playwright 瀏覽器尚未安裝"
    )
  })

  it("explains every worker-emitted error code", () => {
    expect(explainJobError("no_credentials_configured").hint).toContain("密碼")
    expect(explainJobError("auto_login_failed").hint).toContain("Manual Login")
    expect(explainJobError("manual_login_timeout").hint).toContain(
      "登入 session"
    )
    expect(explainJobError("cancelled")).toEqual({
      title: "工作已被取消",
      hint: null,
    })
  })

  it("passes unknown errors through verbatim", () => {
    expect(explainJobError("  boom  ")).toEqual({ title: "boom", hint: null })
    expect(explainJobError("   ").title).toBe("未知錯誤")
  })
})

describe("summarizeJobResult", () => {
  it("returns nothing for a null result", () => {
    expect(summarizeJobResult(null)).toEqual([])
  })

  it("summarizes a successful auto-login result", () => {
    const rows = summarizeJobResult({
      success: true,
      page_kind: "EVENT",
      cookie_count: 7,
      has_session: true,
    })
    expect(rows.map((r) => r.label)).toEqual([
      "登入",
      "登入狀態",
      "KKTIX cookie",
      "頁面判定",
    ])
    expect(rows[0]).toMatchObject({ value: "成功", tone: "success" })
    expect(rows[2].value).toBe("7 個")
    expect(rows[3].value).toBe("EVENT")
  })

  it("flags a session check without a session as a warning", () => {
    const rows = summarizeJobResult({
      page_kind: "UNKNOWN",
      cookie_count: 0,
      has_session: false,
    })
    expect(rows.map((r) => r.label)).toEqual([
      "登入狀態",
      "KKTIX cookie",
      "頁面判定",
    ])
    expect(rows[0]).toMatchObject({ value: "未登入", tone: "warning" })
  })

  it("ignores fields with unexpected types", () => {
    expect(
      summarizeJobResult({ success: "yes", cookie_count: "3", page_kind: 1 })
    ).toEqual([])
  })
})

describe("formatElapsed", () => {
  it("renders m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00")
    expect(formatElapsed(9_400)).toBe("0:09")
    expect(formatElapsed(65_000)).toBe("1:05")
    expect(formatElapsed(600_000)).toBe("10:00")
  })

  it("clamps negatives to zero", () => {
    expect(formatElapsed(-5_000)).toBe("0:00")
  })
})
