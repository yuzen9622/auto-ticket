"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"

import { Badge } from "@/components/ui/badge"
import { Button } from "../animate-ui/components/buttons/button"
import { formatDateTime } from "@/lib/format"
import { useEventStatusLabel } from "@/lib/i18n/labels"
import type { EventSearchResult } from "@/lib/api/types"

/**
 * 一張活動卡片。
 *
 * 刻意不顯示比對分數、原始網址、主辦代號或任何 API 內部欄位——那些是排序用的
 * 中間產物，對要挑活動的人沒有意義。
 */
export function EventCard({ event }: { event: EventSearchResult }) {
  const t = useTranslations("event")
  const eventStatusLabel = useEventStatusLabel()
  const statusLabel =
    event.status === "UNKNOWN" && !event.detail_loaded
      ? t("statusPendingLoad")
      : eventStatusLabel(event.status)

  const providers = event.ticketing_providers
  const saleStart = event.sale_start_at
    ? formatDateTime(event.sale_start_at)
    : null
  const saleEnd = event.sale_end_at ? formatDateTime(event.sale_end_at) : null
  const eventStart = event.event_start_at
    ? formatDateTime(event.event_start_at)
    : null

  return (
    <li className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 shadow-xs">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-sm leading-snug font-semibold break-words">
          {event.title}
        </h3>
        <p className="text-xs leading-relaxed break-words text-muted-foreground">
          {event.description ?? t("noDescription")}
        </p>
      </div>

      <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
        <div className="flex min-w-0 gap-2">
          <dt className="shrink-0 text-muted-foreground">{t("organizer")}</dt>
          <dd className="min-w-0 break-words">
            {event.organizer ?? t("unknownOrganizer")}
          </dd>
        </div>

        <div className="flex min-w-0 gap-2">
          <dt className="shrink-0 text-muted-foreground">{t("providers")}</dt>
          <dd className="flex min-w-0 flex-wrap gap-1">
            {providers.length === 0 ? (
              <span>{t("unknownProvider")}</span>
            ) : (
              providers.map((provider) => (
                <Badge key={provider.id} variant="outline">
                  {provider.name}
                </Badge>
              ))
            )}
          </dd>
        </div>

        {eventStart && (
          <div className="flex min-w-0 gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("eventDate")}</dt>
            <dd className="tabular min-w-0 break-words">{eventStart}</dd>
          </div>
        )}

        {saleStart && (
          <div className="flex min-w-0 gap-2">
            <dt className="shrink-0 text-muted-foreground">
              {saleEnd ? t("salePeriod") : t("saleStart")}
            </dt>
            <dd className="tabular min-w-0 break-words">
              {saleStart}
              {saleEnd ? ` ~ ${saleEnd}` : ""}
            </dd>
          </div>
        )}

        {!saleStart && saleEnd && (
          <div className="flex min-w-0 gap-2">
            <dt className="shrink-0 text-muted-foreground">{t("saleEnd")}</dt>
            <dd className="tabular min-w-0 break-words">~ {saleEnd}</dd>
          </div>
        )}

        <div className="flex min-w-0 gap-2">
          <dt className="shrink-0 text-muted-foreground">{t("status")}</dt>
          <dd className="min-w-0">{statusLabel}</dd>
        </div>
      </dl>

      <div className="flex justify-end">
        <Button asChild size="sm" variant="default">
          <Link href={`/tasks/${encodeURIComponent(event.id)}/new`}>
            {t("choose")}
          </Link>
        </Button>
      </div>
    </li>
  )
}
