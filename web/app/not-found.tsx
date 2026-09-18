"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"

import { EmptyState } from "@/components/terminal/empty-state"

export default function NotFound() {
  const t = useTranslations("common")
  return (
    <EmptyState
      message={t("notFoundTitle")}
      action={<Link href="/">{t("backToSearch")}</Link>}
    />
  )
}
