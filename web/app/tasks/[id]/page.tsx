"use client"

import * as React from "react"

import { LiveConsole } from "@/components/console/live-console"

/** `/tasks/{taskId}` —— 任務儀表板。 */
export default function TaskConsolePage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = React.use(params)
  return <LiveConsole taskId={id} />
}
