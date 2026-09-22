"use client"

import Link from "next/link"
import { useTranslations } from "next-intl"

import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
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
export function EventCard({
  event,
  awaitingStatus = true,
}: {
  event: EventSearchResult
  /**
   * 票況是否還在確認。預設 true——單獨使用這張卡片時，`UNKNOWN` 就當成還在等；
   * 搜尋頁知道輪詢已經結束，會傳 false 進來把骨架收掉。
   */
  awaitingStatus?: boolean
}) {
  const t = useTranslations("event")
  const eventStatusLabel = useEventStatusLabel()
  // 票況是搜尋之後才非同步確認的。還沒確認完時放骨架動畫，不要放一句「確認中」
  // 的文字——那會讓人以為它是一種票況。文字只留給讀螢幕的人。
  // 但等不到就要收手：骨架一直轉下去看起來像壞掉，那時改印「狀態未確認」。
  const statusPending =
    awaitingStatus && event.status === "UNKNOWN" && !event.detail_loaded

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

        <div className="flex min-w-0 items-center gap-2">
          <dt className="shrink-0 text-muted-foreground">{t("status")}</dt>
          <dd className="min-w-0" aria-busy={statusPending || undefined}>
            {statusPending ? (
              <>
                <Skeleton className="h-4 w-16" aria-hidden />
                <span className="sr-only">{t("statusPendingLoad")}</span>
              </>
            ) : (
              eventStatusLabel(event.status)
            )}
          </dd>
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
