"use client"

import * as React from "react"

import { TaskForm } from "@/components/tasks/task-form"

/** `/tasks/{eventId}/new` —— 指定活動的搶票任務表單。 */
export default function NewTaskForEventPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = React.use(params)
  return <TaskForm eventId={id} />
}
