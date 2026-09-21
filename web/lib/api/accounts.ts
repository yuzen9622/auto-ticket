import { apiFetch } from "./client"
import type {
  AccountStatusResponse,
  JobOut,
  LoginRequest,
  StoreCookieRequest,
  StoreCredentialsRequest,
} from "./types"

export function getAccountStatus(
  platform: string
): Promise<AccountStatusResponse> {
  return apiFetch<AccountStatusResponse>(
    `/api/v1/accounts/${encodeURIComponent(platform)}/status`
  )
}

/** 憑證只往後端加密 vault 送，送出後前端立即清空 state、永不落 localStorage。 */
export function storeCredentials(
  platform: string,
  body: StoreCredentialsRequest
): Promise<void> {
  return apiFetch<void>(
    `/api/v1/accounts/${encodeURIComponent(platform)}/credentials`,
    { method: "PUT", body }
  )
}

export function storeCookie(
  platform: string,
  body: StoreCookieRequest
): Promise<void> {
  return apiFetch<void>(
    `/api/v1/accounts/${encodeURIComponent(platform)}/cookie`,
    { method: "POST", body }
  )
}

export function eraseCredentials(platform: string): Promise<void> {
  return apiFetch<void>(
    `/api/v1/accounts/${encodeURIComponent(platform)}/credentials`,
    { method: "DELETE" }
  )
}

/** profile 是 query 參數，不是 body（src/api/routers/accounts.py:75）。 */
export function requestSessionCheck(
  platform: string,
  profile = "live"
): Promise<JobOut> {
  return apiFetch<JobOut>(
    `/api/v1/accounts/${encodeURIComponent(platform)}/session/check`,
    { method: "POST", query: { profile } }
  )
}

export function requestLogin(
  platform: string,
  body: LoginRequest
): Promise<JobOut> {
  return apiFetch<JobOut>(
    `/api/v1/accounts/${encodeURIComponent(platform)}/login`,
    {
      method: "POST",
      body,
    }
  )
}

export function getAccountJob(jobId: string): Promise<JobOut> {
  return apiFetch<JobOut>(`/api/v1/accounts/jobs/${encodeURIComponent(jobId)}`)
}

const TERMINAL_JOB_STATES = ["DONE", "FAILED", "CANCELLED"]

export function isTerminalJobState(state: string): boolean {
  return TERMINAL_JOB_STATES.includes(state)
}
