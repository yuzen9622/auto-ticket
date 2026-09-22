/**
 * 鏡像 src/api/schemas/*.py 與 src/domain/*.py。
 * 欄位名逐字沿用後端 snake_case —— 刻意不轉 camelCase，避免多一層對照表。
 */
import type { SeatStrategy, TicketPriceOrder } from "@/lib/contract"

/* ---------- domain / preference ---------- */

export interface TicketPriority {
  price: number
  ticket_name_pattern?: string | null
  priority: number
}

export interface SeatPreference {
  adjacent: boolean
  strategy: SeatStrategy
  preferred_zones: string[]
}

/**
 * 開賣前寫得出來、不需要知道票價的挑票規則。
 * 拓元與 ibon 的票價要進到票區頁才看得到，`priorities` 在那之前填不出來。
 */
export interface TicketRule {
  max_price: number | null
  min_price: number | null
  /** 名稱關鍵字，越前面越優先。子字串比對，不是正則。 */
  prefer_name_patterns: string[]
  /** 命中即完全排除，連 fallback_to_any 都繞不過去。 */
  exclude_name_patterns: string[]
  price_order: TicketPriceOrder
}

export interface TicketPreference {
  quantity: number
  priorities: TicketPriority[]
  seat_preference: SeatPreference
  fallback_to_any: boolean
  rule: TicketRule | null
}

export interface UserContactProfile {
  name: string
  phone: string
  email: string
}

export interface AttendeeProfile {
  name: string
  phone: string
  id_number?: string | null
}

export interface VerificationRule {
  pattern: string
  answer: string
  is_regex: boolean
}

/* ---------- tasks ---------- */

/** 正式或測試。付款 adapter 由後端依此決定，前端無法指定 adapter。 */
export type ExecutionMode = "live" | "mock"

export interface CreateTaskRequest {
  event_title: string
  event_url: string
  /** 要等的開賣時間；ISO-8601，必須含時區位移。null 代表活動已在販售，立即執行。 */
  sale_start_at: string | null
  ticket_preference: TicketPreference
  contact_profile: UserContactProfile
  attendees: AttendeeProfile[]
  execution_mode: ExecutionMode
  max_retries: number
  timeout_seconds: number
  verification_rules: VerificationRule[]
  auto_login: boolean
  qualification_code?: string | null
  session_preference?: string | null
  auto_cloudflare?: boolean
  auto_ocr?: boolean
  auto_submit_verification?: boolean
  ocr_model_path?: string | null
  ocr_max_retries?: number
  cloudflare_max_retries?: number
  debug_screenshots_and_logs?: boolean
  profile: string
}

export interface TaskResponse {
  id: string
  event_id: string | null
  status: string
  execution_mode: ExecutionMode
  spec: Record<string, unknown>
  scheduled_at: string | null
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  created_at: string
}

export interface TaskListResponse {
  items: TaskResponse[]
  total: number
  limit: number
  offset: number
}

export interface TaskDetailResponse {
  task: TaskResponse
  job_id: string | null
  job_state: string | null
}

export interface StartTaskResponse {
  accepted: boolean
  task_id: string
  triggered: boolean
}

export interface CancelTaskResponse {
  accepted: boolean
  task_id: string
  status: string
}

/* ---------- events ---------- */

export interface ResolveEventRequest {
  query: string
  orgs?: string[] | null
  persist: boolean
}

export interface TicketTypeOut {
  id: string
  name: string
  price: number
  status: string
  remaining_count: number | null
  raw_id: string | null
}

export interface TicketingProviderOut {
  id: string
  name: string
  event_url: string
}

export interface EventSessionInfo {
  name: string
  start_at?: string | null
  url?: string | null
}

export interface EventOut {
  id: string
  platform: string
  /** 內部主辦代號；顯示一律用 `organizer_name`。 */
  organizer: string
  organizer_name: string | null
  event_slug: string
  title: string
  description: string | null
  canonical_url: string
  ticketing_providers: TicketingProviderOut[]
  status: string
  sale_start_at: string | null
  sale_end_at: string | null
  event_start_at: string | null
  ticket_types: TicketTypeOut[]
  /** false 代表票種與開賣時間尚未由活動頁補齊。 */
  detail_loaded: boolean
  raw_metadata: {
    sessions?: EventSessionInfo[]
    [key: string]: unknown
  } | null
}

/** 一場活動目前的售票狀態；`checked_at` 有值才代表真的確認過。 */
export interface EventStatus {
  id: string
  status: string
  sale_start_at: string | null
  sale_end_at: string | null
  event_start_at: string | null
  detail_loaded: boolean
  checked_at: string | null
}

export interface EventStatusesResponse {
  results: EventStatus[]
  /** 後端是否還在補票況；false 代表不會再有新結果了。 */
  pending: boolean
}

export interface EventSearchResult {
  id: string
  title: string
  description: string | null
  organizer: string | null
  ticketing_providers: TicketingProviderOut[]
  canonical_url: string
  sale_start_at: string | null
  sale_end_at: string | null
  event_start_at: string | null
  status: string
  /** true 代表票種與開賣時間已由活動頁補齊。 */
  detail_loaded: boolean
}

export interface EventSearchResponse {
  query: string
  results: EventSearchResult[]
}

export interface EventCandidateOut {
  title: string
  url: string
  score: number
  matched_by: string
}

export interface ResolveEventResponse {
  auto_selected: boolean
  event: EventOut | null
  candidates: EventCandidateOut[]
}

/* ---------- accounts ---------- */

export interface AccountStatusResponse {
  platform: string
  source: string
  configured: boolean
  masked_account: string | null
  credential_kind?: "password" | "cookie" | null
}

export interface StoreCredentialsRequest {
  account: string
  access_key: string
}

export interface StoreCookieRequest {
  cookies: Record<string, string>
}

export interface LoginRequest {
  mode: "auto" | "manual"
  profile: string
}

export interface JobOut {
  job_id: string
  kind: string
  state: string
  result: Record<string, unknown> | null
  error: string | null
}

/* ---------- experiments ---------- */

export interface ExperimentMetricsOut {
  experiment_id: string
  scheduler_error_ms: number | null
  sale_detection_ms: number | null
  event_page_load_ms: number | null
  ticket_selection_ms: number | null
  seat_selection_ms: number | null
  form_fill_ms: number | null
  verification_ms: number | null
  payment_ms: number | null
  retry_count: number
  selector_fallback_count: number
}

export interface ExperimentEventOut {
  id: number
  experiment_id: string | null
  sequence: number
  timestamp: string
  elapsed_ms: number
  stage: string
  state_from: string | null
  state_to: string | null
  action: string
  details: Record<string, unknown> | null
  screenshot_path: string | null
  /** 相對路徑 `/static/screenshots/<file>`，需以 apiBaseUrl 前綴。 */
  screenshot_url: string | null
}

export interface ExperimentOut {
  id: string
  task_id: string | null
  strategy_used: string
  clock_sync_mode: string
  sale_time_error_ms: number | null
  total_duration_ms: number | null
  final_state: string
  success: boolean
  result_summary: Record<string, unknown> | null
  created_at: string
}

export interface ExperimentListResponse {
  items: ExperimentOut[]
  total: number
  limit: number
  offset: number
}

export interface ExperimentDetailResponse {
  experiment: ExperimentOut
  events: ExperimentEventOut[]
  metrics: ExperimentMetricsOut | null
}

/* ---------- health ---------- */

export interface HealthResponse {
  status: string
  version: string
  broker_ok: boolean
  /** null 代表 Worker 從未上線。 */
  worker_seen_at: string | null
}
