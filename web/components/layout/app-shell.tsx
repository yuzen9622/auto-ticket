"use client"

import * as React from "react"

import { AppSidebar } from "@/components/layout/app-sidebar"
import { Toaster } from "@/components/ui/sonner"
import { SidebarInset, SidebarProvider } from "@/components/animate-ui/components/radix/sidebar"

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider className="min-h-0">
      <AppSidebar />
      <SidebarInset className="flex h-dvh min-h-0 flex-col overflow-hidden bg-background">
        <main className="min-h-0 min-w-0 flex-1 overflow-auto p-4">
          {children}
        </main>
      </SidebarInset>
      <Toaster />
    </SidebarProvider>
  )
}
