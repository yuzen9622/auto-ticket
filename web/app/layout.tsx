import type { Metadata } from "next"
import { Outfit, Huninn, Geist_Mono } from "next/font/google"
import { NextIntlClientProvider } from "next-intl"
import { getMessages } from "next-intl/server"

import "./globals.css"
import { AppShell } from "@/components/layout/app-shell"
import { locale, messages as fallbackMessages } from "@/lib/i18n/config"
import { Providers } from "./providers"

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-outfit",
  display: "swap",
})

const huninn = Huninn({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-huninn",
  display: "swap",
  adjustFontFallback: false,
})

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: fallbackMessages.common.appName,
  description: fallbackMessages.common.appDescription,
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  const messages = await getMessages()

  return (
    <html
      lang={locale}
      className={`${outfit.variable} ${huninn.variable} ${geistMono.variable}`}
    >
      <body className="font-sans antialiased">
        <NextIntlClientProvider locale={locale} messages={messages}>
          <Providers>
            <AppShell>{children}</AppShell>
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  )
}
