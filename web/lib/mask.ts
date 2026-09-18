/**
 * 敏感資料遮蔽：純函式，唯讀檢視與表格一律使用遮蔽值。
 * 這些函式**只負責顯示**，不做還原；後端已遮蔽的值（masked_account）直接原樣顯示。
 */

/** 身分證字號：保留首 1 碼與末 3 碼，中間固定 4 個 `*`（不洩漏原長度）。 */
export function maskIdNumber(value: string | null | undefined): string {
  if (!value) return ""
  const v = value.trim()
  if (v.length <= 4) return "*".repeat(v.length)
  return `${v.slice(0, 1)}****${v.slice(-3)}`
}

/** 電話：只保留末 3 碼。 */
export function maskPhone(value: string | null | undefined): string {
  if (!value) return ""
  const v = value.trim()
  if (v.length <= 3) return "*".repeat(v.length)
  return `${"*".repeat(v.length - 3)}${v.slice(-3)}`
}

/** Email：local part 只保留首 1 碼，domain 原樣保留。 */
export function maskEmail(value: string | null | undefined): string {
  if (!value) return ""
  const v = value.trim()
  const at = v.indexOf("@")
  if (at < 0) return maskIdNumber(v)
  const local = v.slice(0, at)
  const domain = v.slice(at)
  if (local.length === 0) return `****${domain}`
  return `${local.slice(0, 1)}****${domain}`
}

/** 密碼 / access_key：恆回固定長度星號，刻意不看輸入，連長度都不外洩。 */
export function maskSecret(): string {
  return "********"
}
