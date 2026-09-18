import { apiFetch } from "./client"
import type {
  EventOut,
  EventSearchResponse,
  ResolveEventRequest,
  ResolveEventResponse,
} from "./types"

/**
 * 依關鍵字或活動網址搜尋活動。
 *
 * 只送查詢字串——票券平台需要的主辦範圍由後端決定，使用者不必也不應該知道。
 */
export function searchEvents(
  query: string,
  options: { signal?: AbortSignal } = {}
): Promise<EventSearchResponse> {
  return apiFetch<EventSearchResponse>("/api/v1/events/search", {
    query: { q: query },
    signal: options.signal,
  })
}

export function getEvent(
  eventId: string,
  options: { signal?: AbortSignal } = {}
): Promise<EventOut> {
  return apiFetch<EventOut>(`/api/v1/events/${encodeURIComponent(eventId)}`, {
    signal: options.signal,
  })
}

export function listEvents(platform = "kktix"): Promise<EventOut[]> {
  return apiFetch<EventOut[]>("/api/v1/events", { query: { platform } })
}

export function resolveEvent(
  body: ResolveEventRequest
): Promise<ResolveEventResponse> {
  return apiFetch<ResolveEventResponse>("/api/v1/events/resolve", {
    method: "POST",
    body,
  })
}
