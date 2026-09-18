"use client"

import * as React from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"

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

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => getExperiment(experimentId),
  })

  if (isLoading) return <EmptyState message="載入中" />
  if (isError || !data) {
    return (
      <EmptyState
        message="無法載入此實驗"
        hint={error instanceof Error ? error.message : undefined}
        action={<Link href="/experiments">回到實驗列表</Link>}
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
