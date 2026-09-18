import { apiFetch } from "./client"
import type {
  EventOut,
  ResolveEventRequest,
  ResolveEventResponse,
} from "./types"

export function resolveEvent(
  body: ResolveEventRequest
): Promise<ResolveEventResponse> {
  return apiFetch<ResolveEventResponse>("/api/v1/events/resolve", {
    method: "POST",
    body,
  })
}

export function listEvents(platform = "kktix"): Promise<EventOut[]> {
  return apiFetch<EventOut[]>("/api/v1/events", { query: { platform } })
}

export function getEvent(eventId: string): Promise<EventOut> {
  return apiFetch<EventOut>(`/api/v1/events/${encodeURIComponent(eventId)}`)
}

/**
 * 後端在缺少主辦代號時回 400 invalid_request，訊息含 "organizer feed scope"。
 * UI 據此導引使用者補 `orgs`，而不是把 400 當成通用錯誤。
 */
export function needsOrganizerScope(message: string): boolean {
  return message.toLowerCase().includes("organizer feed scope")
}
