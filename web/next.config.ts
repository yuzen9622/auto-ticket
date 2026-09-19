import type { NextConfig } from "next"
import createNextIntlPlugin from "next-intl/plugin"

const nextConfig: NextConfig = {
  // Next 16 預設擋掉跨來源的 dev 資源。開發時若用 127.0.0.1 開頁面（而非 localhost），
  // JS chunk 會連同字型一起被擋，畫面停在伺服器端渲染的狀態、完全不會 hydrate——
  // 症狀是所有互動都沒反應，卻一個 console error 也看不到。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
}

// 單一語系且不加 locale 前綴，所以只用 next-intl 的訊息層，不用它的路由層。
export default createNextIntlPlugin("./i18n/request.ts")(nextConfig)
