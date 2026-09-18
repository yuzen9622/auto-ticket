import * as React from "react"
import { NextIntlClientProvider } from "next-intl"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, type RenderOptions } from "@testing-library/react"

import { TooltipProvider } from "@/components/ui/tooltip"

import { locale, messages } from "@/lib/i18n/config"

/** 測試一律走真實的訊息字典，斷言才能對著使用者真正看到的字。 */
export function renderWithProviders(
  ui: React.ReactElement,
  options: RenderOptions = {}
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider locale={locale} messages={messages}>
        <QueryClientProvider client={client}>
          <TooltipProvider>{children}</TooltipProvider>
        </QueryClientProvider>
      </NextIntlClientProvider>
    )
  }

  return { client, ...render(ui, { wrapper: Wrapper, ...options }) }
}

export { messages }
