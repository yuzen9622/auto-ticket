"use client"

import * as React from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { useTranslations } from "next-intl"

import { MetricsPanel } from "@/components/experiments/metrics-panel"
import { TimelineView } from "@/components/experiments/timeline-view"
import { EmptyState } from "@/components/terminal/empty-state"
import { getExperiment } from "@/lib/api/experiments"

export default function ExperimentDetailPage({
  params,
}: {
  params: Promise<{ experimentId: string }>
}) {
  const { experimentId } = React.use(params)
  const t = useTranslations("history")
  const tc = useTranslations("common")

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => getExperiment(experimentId),
  })

  if (isLoading) return <EmptyState message={tc("loading")} />
  if (isError || !data) {
    return (
      <EmptyState
        message={t("detailNotFound")}
        hint={error instanceof Error ? error.message : undefined}
        action={<Link href="/experiments">{t("backToList")}</Link>}
      />
    )
  }

  return (
    <div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <MetricsPanel experiment={data.experiment} metrics={data.metrics} />
      <TimelineView events={data.events} />
    </div>
  )
}
