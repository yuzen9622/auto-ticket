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
   */
  isSettled: boolean
}

/** 每隔多久回頭問一次還沒確認的票況。 */
const POLL_INTERVAL_MS = 2000
/**
 * 連續幾輪都沒有新的票況被確認就收手。
 *
 * 用「還有沒有進展」而不是固定次數當停止條件：後端一批要補多久，取決於這次搜尋
 * 到幾筆、其中幾筆要開瀏覽器，前端猜不準。固定次數只會兩頭不對——太短會在後端
 * 正要寫回時放棄，太長會讓沒有 Worker 的環境空轉。
 */
const IDLE_POLLS_BEFORE_GIVING_UP = 5
/** 硬上限，避免後端一直有零星進展就無止盡地問下去。 */
const MAX_POLLS = 40

interface PollState {
  key: string
  count: number
  confirmed: number
  idle: number
  lastUpdatedAt: number
}

/**
 * 批次追蹤一組活動的售票狀態。
 *
 * 搜尋只負責把活動交出來，票況由後端在背景一場一場地確認；這個 hook 一次問一整批
 * （後端同一個查詢就撈完，不是每張卡片各打一次），沒有進展了就自己停下來。
 */
export function useEventStatuses(eventIds: string[]): EventStatusFeed {
  const key = eventIds.join(",")
  // 進度跟著這一組活動走：換了搜尋結果就重新計算。
  const polls = React.useRef<PollState>({
    key: "",
    count: 0,
    confirmed: 0,
    idle: 0,
    lastUpdatedAt: 0,
  })
  if (polls.current.key !== key) {
    polls.current = {
      key,
      count: 0,
      confirmed: 0,
      idle: 0,
      lastUpdatedAt: 0,
    }
  }

  const query = useQuery({
    queryKey: ["event-statuses", key],
    queryFn: ({ signal }) => {
      polls.current.count += 1
      return getEventStatuses(key.split(","), { signal })
    },
    enabled: key !== "",
    refetchInterval: (q) => {
      const rows = q.state.data?.results
      if (!rows) return POLL_INTERVAL_MS

      // 只在真的收到新回應時才計算進度；`refetchInterval` 每次重繪都會被呼叫。
      if (q.state.dataUpdatedAt !== polls.current.lastUpdatedAt) {
        polls.current.lastUpdatedAt = q.state.dataUpdatedAt
        const confirmed = rows.filter((row) => row.checked_at).length
        if (confirmed > polls.current.confirmed) {
          polls.current.confirmed = confirmed
          polls.current.idle = 0
        } else {
          polls.current.idle += 1
        }
      }

      if (_allConfirmed(key, rows)) return false
      if (polls.current.idle >= IDLE_POLLS_BEFORE_GIVING_UP) return false
      if (polls.current.count >= MAX_POLLS) return false
      return POLL_INTERVAL_MS
    },
  })

  const rows = query.data?.results
  return React.useMemo(() => {
    const statuses = new Map<string, EventStatus>()
    for (const row of rows ?? []) {
      statuses.set(row.id, row)
    }
    const stalled =
      polls.current.idle >= IDLE_POLLS_BEFORE_GIVING_UP ||
      polls.current.count >= MAX_POLLS
    return {
      statuses,
      isSettled: key === "" || _allConfirmed(key, rows) || stalled,
    }
  }, [rows, key, query.dataUpdatedAt])
}

function _allConfirmed(key: string, rows: EventStatus[] | undefined): boolean {
  if (rows === undefined) return false
  const confirmed = new Set(
    rows.filter((row) => row.checked_at).map((row) => row.id)
  )
  return key.split(",").every((id) => confirmed.has(id))
}
