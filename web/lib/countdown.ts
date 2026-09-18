import type { ClockPhase, ClockTickPayload } from "@/lib/ws/types"

export interface CountdownView {
  /** null 代表還沒收到任何伺服器時鐘訊息。 */
  phase: ClockPhase | null
  /** 剩餘毫秒，恆 ≥ 0；null 代表未知或倒數已結束。 */
  remainingMs: number | null
}

/**
 * 由伺服器的時鐘訊息推導畫面要顯示的倒數。
 *
 * 階段一律以伺服器給的 `phase` 為準——前端不用本地時間猜 Worker 走到哪；
 * 本地時間只用來補兩則訊息之間的秒數，讓數字會動。
 */
export function deriveCountdown(
  clock: ClockTickPayload | null,
  now: number,
  receivedAt: number
): CountdownView {
  if (clock === null) return { phase: null, remainingMs: null }
  if (clock.phase === "finished") return { phase: "finished", remainingMs: null }

  const base =
    clock.phase === "waiting_for_sale"
      ? clock.time_to_sale_ms
      : clock.time_to_timeout_ms

  if (base === null || base === undefined) {
    return { phase: clock.phase, remainingMs: null }
  }

  const elapsed = Math.max(0, now - receivedAt)
  // 夾在 0：過期的倒數是 0，永遠不顯示負數。
  return { phase: clock.phase, remainingMs: Math.max(0, base - elapsed) }
}
