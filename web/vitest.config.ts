import { resolve } from "node:path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(import.meta.dirname, ".") },
  },
  test: {
    // 元件測試需要 DOM；純函式測試在 jsdom 底下一樣跑得動。
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: [
      "lib/__tests__/**/*.test.ts",
      "components/__tests__/**/*.test.tsx",
      "app/__tests__/**/*.test.tsx",
    ],
  },
})
