import messages from "@/messages/zh-TW.json"

/**
 * 只提供 zh-TW，且刻意不在網址加 locale 前綴：
 * 路由維持 `/`、`/tasks`、`/experiments`、`/settings`，不會變成 `/zh-TW/...`。
 */
export const locale = "zh-TW"

export const timeZone = "Asia/Taipei"

export { messages }

export type Messages = typeof messages
