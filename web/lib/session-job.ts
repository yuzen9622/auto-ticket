import { PLATFORM_NAMES, type Platform } from "@/lib/contract"
import type { SemanticTone } from "@/lib/fsm"

/** 非終態的工作會依序走完這三段；終態另以 badge 呈現。 */
export const JOB_PROGRESS_STEPS = ["PENDING", "CLAIMED", "RUNNING"] as const

const JOB_STATE_LABEL: Record<string, string> = {
  PENDING: "排隊中",
  CLAIMED: "Worker 已接手",
  RUNNING: "執行中",
  DONE: "已完成",
  FAILED: "失敗",
  CANCELLED: "已取消",
}

const JOB_STATE_HINT: Record<string, string> = {
  PENDING:
    "已送出，等待 Worker 取件；若長時間停在這裡，請確認 Worker 行程正在執行。",
  CLAIMED: "Worker 已取件，正在啟動 Playwright 瀏覽器。",
  RUNNING: "瀏覽器作業進行中。",
  DONE: "工作已完成。",
  FAILED: "工作失敗，詳見下方原因。",
  CANCELLED: "工作已被取消。",
}

const JOB_KIND_LABEL: Record<string, string> = {
  SESSION_CHECK: "Session 檢查",
  AUTO_LOGIN: "自動登入",
  MANUAL_LOGIN: "人工登入",
}

export function jobStateLabel(state: string): string {
  return JOB_STATE_LABEL[state] ?? state
}

export function jobStateHint(state: string): string {
  return JOB_STATE_HINT[state] ?? "狀態未知。"
}

export function jobKindLabel(kind: string): string {
  return JOB_KIND_LABEL[kind] ?? kind
}

/**
 * 進度列的完成段數：PENDING=1、CLAIMED=2、RUNNING=3、終態=3。
 * 失敗與取消一樣回滿，由 badge 與結果區負責表達「結局不是成功」。
 */
export function jobStepCount(state: string): number {
  const i = (JOB_PROGRESS_STEPS as readonly string[]).indexOf(state)
  if (i >= 0) return i + 1
  return JOB_PROGRESS_STEPS.length
}

export function jobOutcomeTone(state: string): SemanticTone {
  if (state === "DONE") return "success"
  if (state === "FAILED") return "danger"
  if (state === "CANCELLED") return "muted"
  return "accent"
}

export interface JobErrorExplanation {
  title: string
  hint: string | null
}

/**
 * Worker 端的失敗字串多半是機器碼或 Playwright 原文；這裡翻成可行動的句子。
 * 瀏覽器二進位缺失是最常見的開箱即用地雷，必須直接給出安裝指令。
 */
export function explainJobError(
  error: string,
  platform?: string
): JobErrorExplanation {
  const raw = error.trim()
  if (/playwright install|executable doesn't exist/i.test(raw)) {
    return {
      title: "Playwright 瀏覽器尚未安裝",
      hint: "請在專案根目錄執行 `pnpm run setup:browsers`（等同 `uv run playwright install chromium`），完成後重啟 Worker。",
    }
  }
  const platformName = platform
    ? PLATFORM_NAMES[platform as Platform] ?? platform
    : ""

  switch (raw) {
    case "tixcraft_requires_manual_login":
      return {
        title: "拓元售票不支援純帳密自動登入",
        hint: "拓元售票僅提供 Facebook 與 Google 第三方社群登入，請點擊「手動登入」，在開啟的瀏覽器視窗中完成授權並保留 Session。",
      }
    case "ibon_requires_manual_login":
      return {
        title: "ibon 售票需手動登入",
        hint: "ibon 售票登入具備圖形驗證碼，請點擊「手動登入」，在瀏覽器視窗中完成登入與驗證。",
      }
    case "no_credentials_configured":
      return {
        title: "尚未設定帳號與密碼",
        hint: platformName
          ? `請先在「帳號與密碼」面板填入 ${platformName} 帳號與密碼並儲存，再重試自動登入。`
          : "請先在「帳號與密碼」面板填入帳號與密碼並儲存，再重試自動登入。",
      }
    case "auto_login_failed":
      return {
        title: "自動登入失敗",
        hint: platformName
          ? `帳號或密碼可能有誤，或 ${platformName} 這次要求人工驗證；可改用手動登入（Manual Login）完成一次。`
          : "帳號或密碼可能有誤，或該平台本次要求人工驗證；可改用手動登入（Manual Login）完成一次。",
      }
    case "manual_login_timeout":
      return {
        title: "人工登入逾時",
        hint: "Worker 等待期間未偵測到登入 session；請重新送出並在瀏覽器視窗內完成登入。",
      }
    case "cancelled":
      return { title: "工作已被取消", hint: null }
    default:
      return { title: raw === "" ? "未知錯誤" : raw, hint: null }
  }
}

export interface JobResultRow {
  label: string
  value: string
  tone: SemanticTone
}

function asBoolean(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null
}

/** 把 Worker 回傳的 result 攤成人看得懂的列；只保留使用者關心的登入狀態。 */
export function summarizeJobResult(
  result: Record<string, unknown> | null,
  _platform: string = "kktix"
): JobResultRow[] {
  if (result === null) return []
  const rows: JobResultRow[] = []

  const isLoggedIn =
    result.login_state === "LOGGED_IN" ||
    result.page_kind === "LOGGED_IN"

  rows.push({
    label: "登入狀態",
    value: isLoggedIn ? "已登入" : "未登入",
    tone: isLoggedIn ? "success" : "warning",
  })

  return rows
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, "0")}`
}
