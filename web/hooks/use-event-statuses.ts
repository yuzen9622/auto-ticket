"use client"

import * as React from "react"
import { useQuery } from "@tanstack/react-query"

import { getEventStatuses } from "@/lib/api/events"
import type { EventStatus } from "@/lib/api/types"

export interface EventStatusFeed {
  /** 已經回報過的票況，依活動 id 索引。 */
  statuses: Map<string, EventStatus>
  /**
   * 是否已經不會再有新票況了（全部確認完，或後端不再有進展）。
   *
   * 呼叫端要靠它決定「還在等」的骨架該收起來——後端如果沒有 Worker 在跑，那些
   * 要瀏覽器才看得到的票況永遠不會被確認，骨架就會一直轉下去，看起來像壞掉。
   * 判斷依據是後端回報的 `pending`，不是前端猜的等待輪數。
   */
  isSettled: boolean
}

/** 每隔多久回頭問一次還沒確認的票況。 */
const POLL_INTERVAL_MS = 2000
/**
 * 硬上限，避免後端一直回報「還在補」就無止盡地問下去。
 *
 * 正常情況輪到後端說 `pending: false` 就停了；這是後端卡住時的保險絲。
 */
const MAX_POLLS = 60

interface PollState {
  key: string
  count: number
}

/**
 * 批次追蹤一組活動的售票狀態。
 *
 * 搜尋只負責把活動交出來，票況由後端在背景一場一場地確認；這個 hook 一次問一整批
 * （後端同一個查詢就撈完，不是每張卡片各打一次），沒有進展了就自己停下來。
 */
export function useEventStatuses(eventIds: string[]): EventStatusFeed {
  const key = eventIds.join(",")
  // 進度跟著這一組活動走：換了搜尋結果就重新計算。計數只在 react-query 的
  // callback 裡讀寫——render 期間碰 ref 會讓 React 在 concurrent 下讀到撕裂的值。
  const polls = React.useRef<PollState>({ key: "", count: 0 })
  // render 要知道「是否已經撞到硬上限」，所以那件事必須是 state 而不是 ref。
  const [cappedKey, setCappedKey] = React.useState<string | null>(null)

  const query = useQuery({
    queryKey: ["event-statuses", key],
    queryFn: ({ signal }) => {
      if (polls.current.key !== key) {
        polls.current = { key, count: 0 }
      }
      polls.current.count += 1
      return getEventStatuses(key.split(","), { signal })
    },
    enabled: key !== "",
    refetchInterval: (q) => {
      const data = q.state.data
      if (!data) return POLL_INTERVAL_MS
      // 後端自己說還在不在補，前端就不必用「連續幾輪沒動靜」去猜。補票況分成
      // 純 HTTP 與瀏覽器兩段，兩段之間的空檔很容易被猜成「已經沒事做了」。
      if (!data.pending || _allConfirmed(key, data.results)) return false
      if (polls.current.key === key && polls.current.count >= MAX_POLLS) {
        setCappedKey(key)
        return false
      }
      return POLL_INTERVAL_MS
    },
  })

  const data = query.data
  return React.useMemo(() => {
    const statuses = new Map<string, EventStatus>()
    for (const row of data?.results ?? []) {
      statuses.set(row.id, row)
    }
    const backendDone = data !== undefined && !data.pending
    return {
      statuses,
      isSettled:
        key === "" ||
        backendDone ||
        _allConfirmed(key, data?.results) ||
        cappedKey === key,
    }
  }, [data, key, cappedKey, query.dataUpdatedAt])
}

function _allConfirmed(key: string, rows: EventStatus[] | undefined): boolean {
  if (rows === undefined) return false
  const confirmed = new Set(
    rows.filter((row) => row.checked_at).map((row) => row.id)
  )
  return key.split(",").every((id) => confirmed.has(id))
}
