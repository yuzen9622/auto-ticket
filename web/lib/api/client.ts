import { apiBaseUrl } from "@/lib/config"

/**
 * 後端統一錯誤契約（src/api/errors.py）：body 恆為
 * `{"error": {"code", "message", "details"}}`。
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown> | null

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> | null = null
  ) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.details = details
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

function buildUrl(path: string, query?: Query): string {
  let url: URL
  try {
    url = new URL(`${apiBaseUrl}${path}`)
  } catch {
    throw new ApiError(
      0,
      "invalid_base_url",
      `API base URL 無效：${apiBaseUrl}，請檢查 NEXT_PUBLIC_API_BASE_URL。`,
      null
    )
  }
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue
      url.searchParams.set(key, String(value))
    }
  }
  return url.toString()
}

export interface RequestOptions {
  method?: string
  query?: Query
  body?: unknown
  signal?: AbortSignal
}

/**
 * 絕不在此記錄 request body —— 它會夾帶 access_key 與身分證字號。
 * 錯誤訊息只取後端回傳的 `message`/`code`。
 */
export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const { method = "GET", query, body, signal } = options

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch {
    throw new ApiError(
      0,
      "network_error",
      "無法連線到 API Server，請確認後端已啟動（預設 http://127.0.0.1:8000）。",
      null
    )
  }

  if (!response.ok) {
    throw await toApiError(response)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.json()) as {
      error?: {
        code?: string
        message?: string
        details?: Record<string, unknown> | null
      }
    }
    const err = payload.error
    if (err && typeof err.code === "string") {
      return new ApiError(
        response.status,
        err.code,
        err.message ?? response.statusText,
        err.details ?? null
      )
    }
  } catch {
    // body 不是預期的錯誤契約形狀 —— 退回 unknown，不外洩原始內容。
  }
  return new ApiError(
    response.status,
    "unknown",
    response.statusText || "請求失敗"
  )
}
