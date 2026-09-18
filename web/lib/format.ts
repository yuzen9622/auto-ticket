/**
 * 後端多數時間欄位是 UTC，但經 SQLite 往返後 `isoformat()` 可能不帶時區位移。
 * JS 會把不帶位移的字串當成**本地時間**，在 UTC+8 會整整差 8 小時，
 * 因此一律補 `Z` 再解析。
 */
export function parseServerDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(value)
  const ms = Date.parse(hasZone ? value : `${value}Z`)
  return Number.isNaN(ms) ? null : new Date(ms)
}

export function formatDateTime(value: string | null | undefined): string {
  const d = parseServerDate(value)
  if (!d) return "—"
  return d.toLocaleString("zh-TW", { hour12: false })
}

export function formatTimeOfDay(d: Date): string {
  const hh = String(d.getHours()).padStart(2, "0")
  const mm = String(d.getMinutes()).padStart(2, "0")
  const ss = String(d.getSeconds()).padStart(2, "0")
  const ms = String(d.getMilliseconds()).padStart(3, "0")
  return `${hh}:${mm}:${ss}.${ms}`
}

/** 相對時間的形狀；文案由 i18n 提供，這裡只決定用哪一個單位。 */
export type RelativeTime =
  | { unit: "never" }
  | { unit: "justNow" }
  | { unit: "seconds" | "minutes" | "hours" | "days"; value: number }

export function relativeTime(
  value: string | null | undefined,
  now = Date.now()
): RelativeTime {
  const d = parseServerDate(value)
  if (!d) return { unit: "never" }
  const diff = Math.max(0, now - d.getTime())
  if (diff < 1000) return { unit: "justNow" }
  const s = Math.floor(diff / 1000)
  if (s < 60) return { unit: "seconds", value: s }
  const m = Math.floor(s / 60)
  if (m < 60) return { unit: "minutes", value: m }
  const h = Math.floor(m / 60)
  if (h < 24) return { unit: "hours", value: h }
  return { unit: "days", value: Math.floor(h / 24) }
}

/** 毫秒 → 人眼可讀；null 顯示 `—`（不顯示 0，避免與「真的是 0」混淆）。 */
export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  const abs = Math.abs(value)
  if (abs < 1000) return `${value.toFixed(abs < 10 ? 2 : 0)} ms`
  if (abs < 60_000) return `${(value / 1000).toFixed(2)} s`
  const totalSeconds = value / 1000
  const m = Math.floor(Math.abs(totalSeconds) / 60)
  const s = Math.abs(totalSeconds) % 60
  return `${totalSeconds < 0 ? "-" : ""}${m}m ${s.toFixed(1)}s`
}

/**
 * 倒數專用：固定 `HH:MM:SS` 寬度，配合 tabular-nums 不跳動。
 * 負數一律夾成 0——倒數過了頭是 `00:00:00`，不是負的時間。
 */
export function formatCountdown(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "--:--:--"
  const total = Math.floor(Math.max(0, ms) / 1000)
  const hh = String(Math.floor(total / 3600)).padStart(2, "0")
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0")
  const ss = String(total % 60).padStart(2, "0")
  return `${hh}:${mm}:${ss}`
}

export function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—"
  return `NT$ ${value.toLocaleString("zh-TW")}`
}

export function shortId(id: string, head = 12): string {
  return id.length <= head ? id : `${id.slice(0, head)}…`
}

/** 送給後端的 `sale_start_at` 一律帶時區位移（R13）。 */
export function toOffsetIso(local: string): string {
  const d = new Date(local)
  if (Number.isNaN(d.getTime())) return local
  const tzMinutes = -d.getTimezoneOffset()
  const sign = tzMinutes >= 0 ? "+" : "-"
  const abs = Math.abs(tzMinutes)
  const oh = String(Math.floor(abs / 60)).padStart(2, "0")
  const om = String(abs % 60).padStart(2, "0")
  const pad = (n: number, w = 2) => String(n).padStart(w, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `${sign}${oh}:${om}`
  )
}

/** `datetime-local` 輸入框需要不帶時區的本地字串。 */
export function toLocalInputValue(value: string | null | undefined): string {
  const d = parseServerDate(value)
  if (!d) return ""
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}
