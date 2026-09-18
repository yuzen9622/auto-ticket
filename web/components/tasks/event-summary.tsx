"use client"

import { useTranslations } from "next-intl"

import { Panel } from "@/components/terminal/panel"
import { Badge } from "@/components/ui/badge"
import type { EventOut } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"
import { useEventStatusLabel } from "@/lib/i18n/labels"

/** 活動摘要：只給人看得懂的名稱，不出現任何內部欄位名。 */
export function EventSummary({ event }: { event: EventOut }) {
  const t = useTranslations("event")
  const common = useTranslations("common")
  const eventStatusLabel = useEventStatusLabel()

  const rows: { label: string; value: React.ReactNode }[] = [
    {
      label: t("organizer"),
      value: event.organizer_name ?? t("unknownOrganizer"),
    },
    {
      label: t("providers"),
      value:
        event.ticketing_providers.length === 0 ? (
          t("unknownProvider")
        ) : (
          <span className="flex flex-wrap gap-1">
            {event.ticketing_providers.map((provider) => (
              <Badge key={provider.id} variant="outline">
                {provider.name}
              </Badge>
            ))}
          </span>
        ),
    },
    {
      label: t("eventDate"),
      value: event.event_start_at
        ? formatDateTime(event.event_start_at)
        : common("none"),
    },
    {
      label: t("saleStart"),
      value: event.sale_start_at
        ? formatDateTime(event.sale_start_at)
        : common("none"),
    },
    { label: t("status"), value: eventStatusLabel(event.status) },
  ]

  return (
    <Panel title={t("summaryHeading")}>
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-sm font-semibold break-words">{event.title}</h2>
          <p className="text-xs leading-relaxed break-words text-muted-foreground">
            {event.description ?? t("noDescription")}
          </p>
        </div>

        {!event.detail_loaded && (
          <p className="rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
            {t("incomplete")}
          </p>
        )}

        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="flex min-w-0 gap-2">
              <dt className="shrink-0 text-muted-foreground">{row.label}</dt>
              <dd className="min-w-0 break-words">{row.value}</dd>
            </div>
          ))}
        </dl>

        <a
          href={event.canonical_url}
          target="_blank"
          rel="noreferrer"
          className="w-fit text-xs text-primary underline underline-offset-2"
        >
          {t("openEventPage")}
        </a>
      </div>
    </Panel>
  )
}
