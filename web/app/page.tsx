import { Suspense } from "react"

import { EventSearch } from "@/components/events/event-search"
import { EmptyState } from "@/components/terminal/empty-state"
import { messages } from "@/lib/i18n/config"

/** 首頁就是新增任務的入口：先搜尋活動，選好才進入搶票資訊表單。 */
export default function NewTaskEntryPage() {
  return (
    <Suspense fallback={<EmptyState message={messages.common.loading} />}>
      <EventSearch />
    </Suspense>
  )
}
