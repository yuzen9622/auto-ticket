import type { NextConfig } from "next"
import createNextIntlPlugin from "next-intl/plugin"

const nextConfig: NextConfig = {}

// 單一語系且不加 locale 前綴，所以只用 next-intl 的訊息層，不用它的路由層。
export default createNextIntlPlugin("./i18n/request.ts")(nextConfig)
