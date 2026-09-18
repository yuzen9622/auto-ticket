import { parseServerDate } from "@/lib/format"
import { apiFetch } from "./client"
import type { HealthResponse } from "./types"

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/healthz")
}

/** Worker 心跳超過 60s 未更新即視為離線。 */
export const WORKER_STALE_MS = 60_000

export function isWorkerOnline(
  workerSeenAt: string | null,
  now = Date.now()
): boolean {
  const seen = parseServerDate(workerSeenAt)
  if (!seen) return false
  return now - seen.getTime() <= WORKER_STALE_MS
}
