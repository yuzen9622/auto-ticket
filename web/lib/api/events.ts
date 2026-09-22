import { apiFetch } from "./client"
import type {
  EventOut,
  EventSearchResponse,
  EventStatusesResponse,
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
  options: { platform?: string; signal?: AbortSignal } = {}
): Promise<EventSearchResponse> {
  const queryParams: Record<string, string> = { q: query }
  if (options.platform) {
    queryParams.platform = options.platform
  }
  return apiFetch<EventSearchResponse>("/api/v1/events/search", {
    query: queryParams,
    signal: options.signal,
  })
}

/**
 * 批次查售票狀態。
 *
 * 搜尋不等狀態算完就先把活動交出來，狀態由後端在背景補；前端拿到活動之後再用
 * 這支把每張卡片的票況接上去，這樣搜尋不會為了票況卡住十幾秒。
 */
export function getEventStatuses(
  eventIds: string[],
  options: { signal?: AbortSignal } = {}
): Promise<EventStatusesResponse> {
  return apiFetch<EventStatusesResponse>("/api/v1/events/statuses", {
    query: { ids: eventIds.join(",") },
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
