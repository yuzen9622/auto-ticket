import type { Metadata } from "next"
import { Geist_Mono, JetBrains_Mono } from "next/font/google"

import "./globals.css"
import { AppShell } from "@/components/layout/app-shell"
import { Providers } from "./providers"

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
})

export const metadata: Metadata = {
  title: "auto-ticket",
  description: "auto-ticket 購票任務控制台",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="zh-Hant"
      className={`${geistMono.variable} ${jetbrainsMono.variable}`}
    >
      <body className="font-mono antialiased">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  )
}
