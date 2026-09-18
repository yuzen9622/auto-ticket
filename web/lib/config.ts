/** 零設定預設值：無 `.env.local` 也能直接對上本機 API。 */
export const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"

export const wsBaseUrl =
  process.env.NEXT_PUBLIC_WS_BASE_URL ?? "ws://127.0.0.1:8000"

/** 後端回傳的截圖是相對路徑；前端在 :3000，必須補上 API origin。 */
export function screenshotUrl(relative: string): string {
  if (/^https?:\/\//i.test(relative)) return relative
  return `${apiBaseUrl}${relative.startsWith("/") ? "" : "/"}${relative}`
}
