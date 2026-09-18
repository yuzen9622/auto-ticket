"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { FlaskConical, LayoutDashboard, Plus, Settings } from "lucide-react"

import { cn } from "@/lib/utils"

const NAV = [
  {
    href: "/",
    label: "儀表板",
    short: "DASH",
    icon: LayoutDashboard,
    exact: true,
  },
  {
    href: "/tasks/new",
    label: "建立任務",
    short: "NEW",
    icon: Plus,
    exact: true,
  },
  {
    href: "/experiments",
    label: "實驗紀錄",
    short: "EXP",
    icon: FlaskConical,
    exact: false,
  },
  {
    href: "/settings",
    label: "設定",
    short: "CONF",
    icon: Settings,
    exact: false,
  },
]

export function NavRail() {
  const pathname = usePathname()

  return (
    <nav
      aria-label="主導覽"
      className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-[var(--oc-border)] bg-[var(--oc-sunken)] py-2"
    >
      {NAV.map((item) => {
        const active = item.exact
          ? pathname === item.href
          : pathname.startsWith(item.href)
        const Icon = item.icon
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            title={item.label}
            className={cn(
              "flex w-12 flex-col items-center gap-0.5 rounded-[4px] border border-transparent py-2 no-underline transition-colors duration-150 ease-out",
              active
                ? "border-[var(--oc-border)] bg-[var(--oc-surface-2)] text-[var(--oc-accent)]"
                : "text-[var(--oc-muted)] hover:bg-[var(--oc-surface-2)] hover:text-[var(--oc-fg)]"
            )}
          >
            <Icon className="size-4" aria-hidden />
            <span className="text-[9px] tracking-wider">{item.short}</span>
          </Link>
        )
      })}
    </nav>
  )
}
