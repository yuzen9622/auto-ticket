import * as React from "react"

import { NavRail } from "@/components/layout/nav-rail"
import { StatusBar } from "@/components/layout/status-bar"
import { Toaster } from "@/components/ui/sonner"

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh min-h-0 flex-col bg-[var(--oc-bg)]">
      <StatusBar />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <NavRail />
        {/* 唯一的捲動容器：內容再高也只捲這裡，不會長出全頁垂直捲軸。 */}
        <main className="oc-scroll min-h-0 min-w-0 flex-1 overflow-auto p-4">
          {children}
        </main>
      </div>
      <Toaster />
    </div>
  )
}
