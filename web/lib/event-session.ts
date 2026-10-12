import type { EventOut, EventSessionInfo } from "@/lib/api/types"
import { isSellingNow } from "@/lib/ticketing-time"

/** Stable performance identity; old metadata retains a date-qualified fallback. */
export function sessionPreference(session: EventSessionInfo): string {
  if (session.key) return `id:${session.key}`
  if (session.url) return `url:${session.url}`
  return [session.name, session.display_date].filter(Boolean).join(" ")
}

export function sessionSale(event: EventOut | undefined, preference: string, now: number) {
  const sessions = event?.raw_metadata?.sessions ?? []
  const selected = sessions.find((s) => sessionPreference(s) === preference)
    ?? (sessions.length === 1 ? sessions[0] : undefined)
  const saleStartAt = selected?.sale_start_at ?? event?.sale_start_at
  const selling = selected?.can_buy !== undefined
    ? selected.can_buy && !selected.sold_out
    : isSellingNow(event?.status, saleStartAt, now)
  return { saleStartAt, selling }
}
