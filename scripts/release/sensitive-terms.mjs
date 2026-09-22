/**
 * 敏感詞 denylist 的單一事實來源。
 *
 * repo 轉 public 之後，commit subject 與 Release notes 是最多人會讀到的兩個面，
 * 而它們用的必須是同一份清單——兩邊各抄一份，遲早會有一邊漏掉新詞。
 *
 * 命中的不是「祕密」，而是會告訴票務平台「我們在盯哪裡」的工程細節：
 * 選擇器、風控與反偵測機制、重試與預熱時序。
 */

export const SENSITIVE_TERMS = [
  "selector",
  "xpath",
  "css\\s",
  "captcha",
  "驗證碼",
  "cloudflare",
  "turnstile",
  "anti[- ]?bot",
  "反偵測",
  "風控",
  "风控",
  "fingerprint",
  "webdriver",
  "stealth",
  "bypass",
  "繞過",
  "rate[- ]?limit",
  "backoff",
  "retry",
  "warmup",
  "預熱",
  "T-\\d",
]

/** 每次呼叫回傳新的 RegExp：帶 `g` 的 regex 有 `lastIndex` 狀態，共用同一個會漏配。 */
export function sensitiveTermRe(flags = "i") {
  return new RegExp(SENSITIVE_TERMS.join("|"), flags)
}

/** 回傳命中的詞（去重、保留原文大小寫）；沒命中回空陣列。 */
export function findSensitiveTerms(text) {
  const matches = String(text).match(sensitiveTermRe("gi")) ?? []
  return [...new Set(matches.map((m) => m.trim()))]
}
