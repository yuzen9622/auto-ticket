"use client"

import * as React from "react"

import { LiveConsole } from "@/components/console/live-console"

export default function TaskConsolePage({
  params,
}: {
  params: Promise<{ taskId: string }>
}) {
  const { taskId } = React.use(params)
  return <LiveConsole taskId={taskId} />
}
