"use client"

import * as React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useTranslations } from "next-intl"
import { History, ListChecks, Plus, Settings } from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
} from "@/components/animate-ui/components/radix/sidebar"

type NavKey = "newTask" | "tasks" | "history" | "settings"

interface NavItem {
  key: NavKey
  href: string
  icon: typeof Plus
  /** 哪些路徑算在這一項底下——`/tasks/{id}/new` 屬於新增任務，不是任務管理。 */
  matches: (pathname: string) => boolean
}

const NAV: readonly NavItem[] = [
  {
    key: "newTask",
    href: "/",
    icon: Plus,
    matches: (p) => p === "/" || /^\/tasks\/[^/]+\/new$/.test(p),
  },
  {
    key: "tasks",
    href: "/tasks",
    icon: ListChecks,
    matches: (p) => p === "/tasks" || /^\/tasks\/[^/]+$/.test(p),
  },
  {
    key: "history",
    href: "/experiments",
    icon: History,
    matches: (p) => p.startsWith("/experiments"),
  },
  {
    key: "settings",
    href: "/settings",
    icon: Settings,
    matches: (p) => p.startsWith("/settings"),
  },
]

export function AppSidebar() {
  const pathname = usePathname()
  const t = useTranslations("nav")

  return (
    <Sidebar collapsible="icon" aria-label={t("label")}>
      <SidebarHeader className="flex h-12 flex-row items-center justify-between px-2 py-2">
        <SidebarTrigger
          tabIndex={-1}
          aria-label={t("toggleSidebar")}
          className="size-8 border-none text-muted-foreground hover:text-foreground"
        />
      </SidebarHeader>

      <SidebarContent className="px-2 py-2">
        <SidebarMenu className="gap-1">
          {NAV.map((item) => {
            const label = t(item.key)
            const active = item.matches(pathname)
            const Icon = item.icon
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  asChild
                  isActive={active}
                  tooltip={label}
                  className="h-10 px-3 text-sm font-medium"
                >
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className="flex items-center gap-3"
                  >
                    <Icon className="size-5 shrink-0" aria-hidden />
                    <span className="truncate text-sm">{label}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            )
          })}
        </SidebarMenu>
      </SidebarContent>

      <SidebarRail aria-label={t("toggleSidebarEdge")} />
    </Sidebar>
  )
}
