"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

import { AppSidebar } from "@/components/layout/app-sidebar"
import { Toaster } from "@/components/ui/sonner"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/animate-ui/components/radix/sidebar"

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("nav")

  return (
    <SidebarProvider className="min-h-0">
      <AppSidebar />
      {/* SidebarInset 本身就是 <main>，所以底下只放區塊元素。 */}
      <SidebarInset className="flex h-dvh min-h-0 flex-col overflow-hidden bg-background">
        {/* 窄螢幕的側邊欄整個收進抽屜，抽屜關著時這顆按鈕是唯一的開啟入口。 */}
        <header className="flex h-12 shrink-0 items-center border-b px-2 md:hidden">
          <SidebarTrigger
            aria-label={t("openSidebar")}
            className="size-8 border-none text-muted-foreground hover:text-foreground"
          />
        </header>
        <div className="min-h-0 min-w-0 flex-1 overflow-auto p-4">
          {children}
        </div>
      </SidebarInset>
      <Toaster />
    </SidebarProvider>
  )
}
