import { TaskTable } from "@/components/tasks/task-table"

export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-4">
      <TaskTable />
    </div>
  )
}
