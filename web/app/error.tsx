"use client"

import { useTranslations } from "next-intl"

import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/terminal/empty-state"

export default function GlobalError({ reset }: { reset: () => void }) {
  const t = useTranslations("common")
  return (
    <EmptyState
      message={t("unexpectedError")}
      action={
        <Button variant="outline" size="sm" onClick={reset}>
          {t("retry")}
        </Button>
      }
    />
  )
}
