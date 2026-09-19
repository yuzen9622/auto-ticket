import { parseServerDate } from "@/lib/format"

export const MINUTE_MS = 60_000

/**
 * 正規化到分鐘。
 *
 * `datetime-local` 只到分鐘，若拿含秒數的「現在」去比，剛帶入的預設值會在
 * 下一次驗證時被判成過期。所有比較都走這一層，誤差就不會變成假錯誤。
 */
export function floorToMinute(ms: number): number {
  return Math.floor(ms / MINUTE_MS) * MINUTE_MS
}

/** `datetime-local` 需要不帶時區的本地字串。 */
export function msToLocalInput(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

/**
 * 活動現在就在賣嗎？
 *
 * 在賣就沒有「搶票時間」這回事：任務建立後直接進登記頁下單，不走預約開賣流程。
 * 判斷同時看狀態與開賣時間——狀態還停在 `ANNOUNCED` 但開賣時間已過的活動，
 * 一樣沒有東西可等。
 */
export function isSellingNow(
  status: string | null | undefined,
  saleStartAt: string | null | undefined,
  now: number
): boolean {
  if (status === "ON_SALE") return true
  const sale = parseServerDate(saleStartAt)?.getTime()
  return sale !== undefined && sale <= now
}

/**
 * 搶票時間的預設值：活動開賣時間還沒到就用它，否則用現在。
 * 活動沒有開賣時間時同樣退回現在（呼叫端負責提示使用者確認）。
 */
export function defaultTicketingTimeMs(
  saleStartAt: string | null | undefined,
  now: number
): number {
  const sale = parseServerDate(saleStartAt)?.getTime()
  const base = sale !== undefined && sale > now ? sale : now
  return floorToMinute(base)
}

export function defaultTicketingTimeLocal(
  saleStartAt: string | null | undefined,
  now: number
): string {
  return msToLocalInput(defaultTicketingTimeMs(saleStartAt, now))
}

/** `datetime-local` 的 min：目前這一分鐘。 */
export function minTicketingTimeLocal(now: number): string {
  return msToLocalInput(floorToMinute(now))
}

/**
 * 是否已經過期。比較基準是「現在這一分鐘的開始」，所以剛帶入的現在不算過期，
 * 但停在頁面上跨過一分鐘之後就會算過期——送出前必須重跑這個判斷。
 */
export function isTicketingTimeInPast(local: string, now: number): boolean {
  const chosen = new Date(local).getTime()
  if (Number.isNaN(chosen)) return false
  return chosen < floorToMinute(now)
}

export function isTicketingTimeParsable(local: string): boolean {
  return local !== "" && !Number.isNaN(new Date(local).getTime())
}
