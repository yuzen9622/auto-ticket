"use client"

import * as React from "react"
import { useQuery } from "@tanstack/react-query"

import { getEventStatuses } from "@/lib/api/events"
import type { EventStatus } from "@/lib/api/types"

export interface EventStatusFeed {
  /** 已經回報過的票況，依活動 id 索引。 */
  statuses: Map<string, EventStatus>
  /**
   * 是否已經不會再有新票況了（全部確認完，或問到上限放棄）。
   *
   * 呼叫端要靠它決定「還在等」的骨架該收起來——沒有 Worker 在跑時票況永遠不會
   * 被確認，骨架就會一直轉下去，看起來像壞掉。
   */
  isSettled: boolean
}

/** 每隔多久回頭問一次還沒確認的票況。 */
const POLL_INTERVAL_MS = 2000
/**
 * 最多問幾次。
 *
 * 要瀏覽器才看得到的票況（拓元的場次表、KKTIX 與 ibon 的售完）是由 Worker 補的；
 * 沒有 Worker 在跑時那些活動永遠不會被確認，無上限的輪詢就會一直打下去。
 */
const MAX_POLLS = 15

/**
 * 批次追蹤一組活動的售票狀態。
 *
 * 搜尋只負責把活動交出來，票況由後端在背景一場一場地確認；這個 hook 一次問一整批
 * （後端同一個查詢就撈完，不是每張卡片各打一次），確認完就自己停下來。
 */
export function useEventStatuses(eventIds: string[]): EventStatusFeed {
  const key = eventIds.join(",")
  // 輪詢次數跟著這一組活動走：換了搜尋結果就重新計次。
  const polls = React.useRef({ key: "", count: 0 })
  if (polls.current.key !== key) {
    polls.current = { key, count: 0 }
  }

  const query = useQuery({
    queryKey: ["event-statuses", key],
    queryFn: ({ signal }) => {
      polls.current.count += 1
      return getEventStatuses(key.split(","), { signal })
    },
    enabled: key !== "",
    refetchInterval: (q) => {
      if (polls.current.count >= MAX_POLLS) return false
      const rows = q.state.data?.results
      if (!rows) return POLL_INTERVAL_MS
      const confirmed = new Set(
        rows.filter((row) => row.checked_at).map((row) => row.id)
      )
      const wanted = key.split(",")
      return wanted.every((id) => confirmed.has(id)) ? false : POLL_INTERVAL_MS
    },
  })

  const rows = query.data?.results
  return React.useMemo(() => {
    const statuses = new Map<string, EventStatus>()
    for (const row of rows ?? []) {
      statuses.set(row.id, row)
    }
    const wanted = key === "" ? [] : key.split(",")
    const allConfirmed =
      rows !== undefined &&
      wanted.every((id) => statuses.get(id)?.checked_at != null)
    return {
      statuses,
      isSettled:
        key === "" || allConfirmed || polls.current.count >= MAX_POLLS,
    }
  }, [rows, key, query.dataUpdatedAt])
}
