import type { NextConfig } from "next"
import createNextIntlPlugin from "next-intl/plugin"

const nextConfig: NextConfig = {
  // 打包發行時只搬 `.next/standalone`，依賴追蹤交給 Next 自己做，不自行重算。
  // 只影響 `next build`，`next dev` 完全不受影響。
  output: "standalone",
  // Next 16 預設擋掉跨來源的 dev 資源。開發時若用 127.0.0.1 開頁面（而非 localhost），
  // JS chunk 會連同字型一起被擋，畫面停在伺服器端渲染的狀態、完全不會 hydrate——
  // 症狀是所有互動都沒反應，卻一個 console error 也看不到。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
}

// 單一語系且不加 locale 前綴，所以只用 next-intl 的訊息層，不用它的路由層。
export default createNextIntlPlugin("./i18n/request.ts")(nextConfig)
