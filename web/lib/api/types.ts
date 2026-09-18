/**
 * 鏡像 src/api/schemas/*.py 與 src/domain/*.py。
 * 欄位名逐字沿用後端 snake_case —— 刻意不轉 camelCase，避免多一層對照表。
 */
import type { SeatStrategy } from "@/lib/contract"

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

export interface TicketPreference {
  quantity: number
  priorities: TicketPriority[]
  seat_preference: SeatPreference
  fallback_to_any: boolean
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

export interface CreateTaskRequest {
  event_title: string
  event_url: string
  /** ISO-8601，必須含時區位移。 */
  sale_start_at: string
  ticket_preference: TicketPreference
  contact_profile: UserContactProfile
  attendees: AttendeeProfile[]
  payment_method: "mock"
  max_retries: number
  timeout_seconds: number
  verification_rules: VerificationRule[]
  auto_login: boolean
  qualification_code?: string | null
  profile: string
}

export interface TaskResponse {
  id: string
  event_id: string | null
  status: string
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

export interface EventOut {
  id: string
  platform: string
  organizer: string
  event_slug: string
  title: string
  canonical_url: string
  status: string
  sale_start_at: string | null
  sale_end_at: string | null
  event_start_at: string | null
  ticket_types: TicketTypeOut[]
  raw_metadata: Record<string, unknown> | null
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
}

export interface StoreCredentialsRequest {
  account: string
  access_key: string
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
