import type { ClientAction, ServerMessageType } from "@/lib/contract"

/** 封包外殼：src/api/schemas/ws.py ServerMessage。 */
interface Envelope<TType extends ServerMessageType, TPayload> {
  type: TType
  task_id: string
  experiment_id?: string | null
  timestamp: string
  payload: TPayload
  /** snapshot 與指令 ACK 不帶 outbox_id（不參與去重）。 */
  outbox_id?: number | null
}

/** src/worker/telemetry_bridge.py */
export interface StateChangedPayload {
  from_state: string
  to_state: string
  event: string
  elapsed_ms: number
}

/** 倒數階段由伺服器判定，前端不得用本地時間自行推測。 */
export type ClockPhase = "waiting_for_sale" | "ticketing" | "finished"

export interface ClockTickPayload {
  server_time: string
  phase: ClockPhase
  /** 已夾在 0 以上；`finished` 階段為 null。 */
  time_to_sale_ms: number | null
  /** 本次搶票剩餘時間，同樣夾在 0 以上；未知或尚未開賣時為 null。 */
  time_to_timeout_ms: number | null
  clock_offset_ms: number
}

export interface ScreenshotPayload {
  state: string
  sequence: number
  /** 相對路徑，需以 apiBaseUrl 前綴。 */
  url: string
}

/** Worker 端錯誤。 */
export interface WorkerErrorPayload {
  name: string
  error_type: string
  error_message: string
}

/** WS 端點自身的協定錯誤（src/api/ws/endpoint.py）。 */
export interface ProtocolErrorPayload {
  reason: "invalid_command" | "task_id_mismatch" | "missing_target_state"
  details?: string
}

export type ErrorPayload = WorkerErrorPayload | ProtocolErrorPayload

/** 連線初始快照。 */
export interface SnapshotLogPayload {
  phase: "snapshot"
  task_status: string
  job_state: string
  experiment_id: string | null
}

/** 指令 ACK。 */
export interface AckLogPayload {
  action: ClientAction
  accepted: boolean
  signal_id: number
}

/** 一般 Worker 日誌。 */
export interface GeneralLogPayload {
  event_type: string
  name: string
  detail?: unknown
}

/** Worker 停下來等真人處理（人機驗證、登入、停在錯的頁面）。 */
export interface HumanGateLogPayload {
  phase: "waiting_for_human"
  page_kind: string
  attempt: number
  hint: string
  event_url?: string
  /** false 代表 Worker 沒有可見視窗，沒有人點得到。 */
  attended?: boolean
  /** false 代表這個瀏覽器過不了人機驗證（只有借用自己的 Chrome 才過得了）。 */
  can_clear_bot_check?: boolean
}

/** Worker 正在被動等待 Cloudflare 的自動挑戰自己跑完（不代為繞過，只是等）。 */
export interface CloudflareGraceLogPayload {
  phase: "cloudflare_grace"
  page_kind: string
  elapsed_s: number
  budget_s: number
  round: number
  max_rounds: number
}

/** 正在辨識圖片驗證碼；attempt / max_retries 對應「（x/y）」。 */
export interface OcrProgressLogPayload {
  phase: "ocr_processing"
  attempt: number
  max_retries: number
}

/** 驗證已完成（含手動輸入被保留的情況），流程即將繼續。 */
export interface VerificationDoneLogPayload {
  phase: "verification_completed"
  kind: string
}

export type TaskLogPayload =
  | SnapshotLogPayload
  | AckLogPayload
  | HumanGateLogPayload
  | CloudflareGraceLogPayload
  | OcrProgressLogPayload
  | VerificationDoneLogPayload
  | GeneralLogPayload

export type ServerMessage =
  | Envelope<"STATE_CHANGED", StateChangedPayload>
  | Envelope<"CLOCK_TICK", ClockTickPayload>
  | Envelope<"SCREENSHOT_CAPTURED", ScreenshotPayload>
  | Envelope<"TASK_LOG", TaskLogPayload>
  | Envelope<"ERROR", ErrorPayload>

export function isSnapshot(p: TaskLogPayload): p is SnapshotLogPayload {
  return (p as SnapshotLogPayload).phase === "snapshot"
}

export function isHumanGate(p: TaskLogPayload): p is HumanGateLogPayload {
  return (p as HumanGateLogPayload).phase === "waiting_for_human"
}

export function isCloudflareGrace(p: TaskLogPayload): p is CloudflareGraceLogPayload {
  return (p as CloudflareGraceLogPayload).phase === "cloudflare_grace"
}

export function isOcrProgress(p: TaskLogPayload): p is OcrProgressLogPayload {
  return (p as OcrProgressLogPayload).phase === "ocr_processing"
}

export function isVerificationDone(p: TaskLogPayload): p is VerificationDoneLogPayload {
  return (p as VerificationDoneLogPayload).phase === "verification_completed"
}

/** src/api/schemas/ws.py ClientCommand。 */
export interface ClientCommand {
  action: ClientAction
  task_id: string
  reason?: string
  /** FORCE_TRANSITION 必填，否則後端回 missing_target_state。 */
  target_state?: string
}
