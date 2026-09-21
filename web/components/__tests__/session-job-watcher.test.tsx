import * as React from "react"
import { NextIntlClientProvider } from "next-intl"
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SessionJobWatcher } from "@/components/settings/session-job-watcher"
import { TooltipProvider } from "@/components/ui/tooltip"
import { locale, messages } from "@/lib/i18n/config"

const api = vi.hoisted(() => ({
  getAccountJob: vi.fn(),
  requestLogin: vi.fn(),
  requestSessionCheck: vi.fn(),
}))

vi.mock("@/lib/api/accounts", () => ({
  getAccountJob: api.getAccountJob,
  requestLogin: api.requestLogin,
  requestSessionCheck: api.requestSessionCheck,
  isTerminalJobState: (state: string) =>
    ["DONE", "FAILED", "CANCELLED"].includes(state),
}))

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    message: vi.fn(),
  },
}))

function renderWatcher() {
  const client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  })

  return render(
    <NextIntlClientProvider locale={locale} messages={messages}>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <SessionJobWatcher platform="ibon" />
        </TooltipProvider>
      </QueryClientProvider>
    </NextIntlClientProvider>
  )
}

describe("SessionJobWatcher", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    focusManager.setFocused(true)
    api.requestLogin.mockResolvedValue({
      job_id: "job-1",
      kind: "AUTO_LOGIN",
      state: "PENDING",
      result: null,
      error: null,
    })
    api.getAccountJob.mockResolvedValue({
      job_id: "job-1",
      kind: "AUTO_LOGIN",
      state: "RUNNING",
      result: null,
      error: null,
    })
  })

  afterEach(() => {
    focusManager.setFocused(undefined)
  })

  it("immediately refreshes a running login job when the settings tab regains focus", async () => {
    renderWatcher()

    fireEvent.click(screen.getByRole("button", { name: "自動登入" }))
    await waitFor(() => expect(api.getAccountJob).toHaveBeenCalledTimes(1))

    api.getAccountJob.mockResolvedValueOnce({
      job_id: "job-1",
      kind: "AUTO_LOGIN",
      state: "DONE",
      result: { success: true, login_state: "LOGGED_IN" },
      error: null,
    })

    act(() => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })

    await waitFor(() => expect(api.getAccountJob).toHaveBeenCalledTimes(2), {
      timeout: 300,
    })
    expect(await screen.findByText("已完成")).toBeInTheDocument()
  })
})
