import * as React from "react"
import { describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "./helpers/render"

const pathname = vi.hoisted(() => ({ current: "/" }))

vi.mock("next/navigation", () => ({
  usePathname: () => pathname.current,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))

import { AppSidebar } from "@/components/layout/app-sidebar"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"

function renderShell(path = "/") {
  pathname.current = path
  return renderWithProviders(
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <SidebarTrigger aria-label="收合或展開側邊欄" />
        <main>內容</main>
      </SidebarInset>
    </SidebarProvider>
  )
}

function sidebarRoot() {
  return document.querySelector('[data-slot="sidebar"][data-state]')
}

describe("AppSidebar", () => {
  it("導覽順序固定為新增任務、任務管理、歷史紀錄、設定", () => {
    renderShell()
    const links = screen.getAllByRole("link")
    expect(links.map((link) => link.textContent?.trim())).toEqual([
      "新增任務",
      "任務管理",
      "歷史紀錄",
      "設定",
    ])
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/",
      "/tasks",
      "/experiments",
      "/settings",
    ])
  })

  it("首頁時「新增任務」是目前頁面", () => {
    renderShell("/")
    expect(screen.getByRole("link", { name: "新增任務" })).toHaveAttribute(
      "aria-current",
      "page"
    )
  })

  it("/tasks/{eventId}/new 歸屬新增任務", () => {
    renderShell("/tasks/ev_abc123/new")
    expect(screen.getByRole("link", { name: "新增任務" })).toHaveAttribute(
      "aria-current",
      "page"
    )
    expect(
      screen.getByRole("link", { name: "任務管理" })
    ).not.toHaveAttribute("aria-current")
  })

  it("/tasks/{taskId} 歸屬任務管理", () => {
    renderShell("/tasks/task_abc123")
    expect(screen.getByRole("link", { name: "任務管理" })).toHaveAttribute(
      "aria-current",
      "page"
    )
    expect(screen.getByRole("link", { name: "新增任務" })).not.toHaveAttribute(
      "aria-current"
    )
  })

  it("歷史紀錄頁時「歷史紀錄」是目前頁面", () => {
    renderShell("/experiments")
    expect(screen.getByRole("link", { name: "歷史紀錄" })).toHaveAttribute(
      "aria-current",
      "page"
    )
  })

  it("桌面版可以收合與展開", async () => {
    const user = userEvent.setup()
    renderShell()
    expect(sidebarRoot()).toHaveAttribute("data-state", "expanded")

    await user.click(screen.getByRole("button", { name: "收合或展開側邊欄" }))
    expect(sidebarRoot()).toHaveAttribute("data-state", "collapsed")

    await user.click(screen.getByRole("button", { name: "收合或展開側邊欄" }))
    expect(sidebarRoot()).toHaveAttribute("data-state", "expanded")
  })

  it("純圖示的收合鈕有無障礙名稱，且能用鍵盤操作", async () => {
    const user = userEvent.setup()
    renderShell()
    const trigger = screen.getByRole("button", { name: "收合或展開側邊欄" })
    trigger.focus()
    await user.keyboard("{Enter}")
    expect(sidebarRoot()).toHaveAttribute("data-state", "collapsed")
  })

  it("導覽連結可以用 Tab 逐一聚焦", async () => {
    const user = userEvent.setup()
    renderShell()
    await user.tab()
    const nav = screen.getByRole("link", { name: "新增任務" })
    // 第一個可聚焦的導覽項就是「新增任務」。
    expect(document.activeElement === nav || nav.contains(document.activeElement)).toBe(
      true
    )
  })

  it("畫面上不存在舊的自製導覽列", () => {
    renderShell()
    expect(screen.queryByText("DASH")).toBeNull()
    expect(screen.queryByText("儀表板")).toBeNull()
    expect(screen.queryByText("實驗紀錄")).toBeNull()
  })
})

describe("行動版側邊欄", () => {
  it("小螢幕以抽屜開啟與關閉，關閉後不擋住主要內容", async () => {
    const user = userEvent.setup()
    // 行動版由 useIsMobile 依 innerWidth 判斷。
    const original = window.innerWidth
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 500,
    })
    window.dispatchEvent(new Event("resize"))

    renderShell()
    await user.click(screen.getByRole("button", { name: "收合或展開側邊欄" }))

    const drawer = await screen.findByRole("dialog")
    expect(within(drawer).getByRole("link", { name: "新增任務" })).toBeVisible()

    await user.keyboard("{Escape}")
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull()
    })
    expect(screen.getByText("內容")).toBeVisible()

    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: original,
    })
  })
})
