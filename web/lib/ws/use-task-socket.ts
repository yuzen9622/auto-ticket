"use client"

import * as React from "react"

import { wsBaseUrl } from "@/lib/config"
import { isTaskFinished } from "@/lib/fsm"
import { appendEntry, toEntry, type LogEntry } from "@/lib/log-buffer"
import type { WsStatus } from "@/lib/task-status"
import type {
  ClientCommand,
  ClockTickPayload,
  ScreenshotPayload,
  ServerMessage,
} from "@/lib/ws/types"
import { isSnapshot } from "@/lib/ws/types"

/** 指數退避：500ms → 1s → 2s → 4s → 8s（上限）。 */
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000]

export interface TaskSocketState {
  status: WsStatus
  entries: LogEntry[]
  clock: ClockTickPayload | null
  clockReceivedAt: number
  screenshots: ScreenshotPayload[]
  currentState: string | null
  visitedStates: string[]
  snapshotStatus: { task_status: string; job_state: string } | null
  send: (cmd: Omit<ClientCommand, "task_id">) => boolean
  clearLogs: () => void
}

export function useTaskSocket(
  taskId: string | null,
  options: { taskStatus?: string | null } = {}
): TaskSocketState {
  const { taskStatus } = options

  const [status, setStatus] = React.useState<WsStatus>("connecting")
  const [entries, setEntries] = React.useState<LogEntry[]>([])
  const [clock, setClock] = React.useState<ClockTickPayload | null>(null)
  // 收到這則時鐘訊息的本地時刻；倒數靠它補兩則訊息之間的秒數。
  const [clockReceivedAt, setClockReceivedAt] = React.useState(0)
  const [screenshots, setScreenshots] = React.useState<ScreenshotPayload[]>([])
  const [currentState, setCurrentState] = React.useState<string | null>(null)
  const [visitedStates, setVisitedStates] = React.useState<string[]>([])
  const [snapshotStatus, setSnapshotStatus] = React.useState<{
    task_status: string
    job_state: string
  } | null>(null)

  const socketRef = React.useRef<WebSocket | null>(null)
  const seenRef = React.useRef<Set<string>>(new Set())
  const seqRef = React.useRef(0)
  const attemptRef = React.useRef(0)
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const closedRef = React.useRef(false)

  const isFinished = Boolean(taskStatus && isTaskFinished(taskStatus))
  const stopRef = React.useRef(isFinished)
  React.useEffect(() => {
    stopRef.current = isFinished
  }, [isFinished])

  const handleMessage = React.useCallback((msg: ServerMessage) => {
    // CLOCK_TICK 是 1Hz ephemeral，不進日誌緩衝，否則數分鐘內淹沒畫面（R3）。
    if (msg.type === "CLOCK_TICK") {
      setClock(msg.payload)
      setClockReceivedAt(Date.now())
    }

    if (msg.type === "STATE_CHANGED") {
      const { to_state } = msg.payload
      setCurrentState(to_state)
      setVisitedStates((prev) =>
        prev.includes(to_state) ? prev : [...prev, to_state]
      )
    }

    if (msg.type === "SCREENSHOT_CAPTURED") {
      const shot = msg.payload
      setScreenshots((prev) =>
        prev.some((s) => s.sequence === shot.sequence && s.url === shot.url)
          ? prev
          : [...prev, shot]
      )
    }

    if (msg.type === "TASK_LOG" && isSnapshot(msg.payload)) {
      setSnapshotStatus({
        task_status: msg.payload.task_status,
        job_state: msg.payload.job_state,
      })
    }

    const entry = toEntry(msg, seqRef.current++)
    setEntries((prev) => appendEntry(prev, entry, seenRef.current))
  }, [])

  React.useEffect(() => {
    if (!taskId) return
    closedRef.current = false

    const connect = () => {
      if (closedRef.current) return
      if (stopRef.current) {
        setStatus("closed")
        return
      }

      setStatus(attemptRef.current === 0 ? "connecting" : "reconnecting")

      let socket: WebSocket
      try {
        socket = new WebSocket(
          `${wsBaseUrl}/ws/tasks/${encodeURIComponent(taskId)}`
        )
      } catch {
        scheduleReconnect()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        attemptRef.current = 0
        setStatus("open")
      }

      socket.onmessage = (ev: MessageEvent<string>) => {
        try {
          handleMessage(JSON.parse(ev.data) as ServerMessage)
        } catch {
          // 非預期封包直接忽略，不讓單一壞訊息中斷整條串流。
        }
      }

      socket.onclose = () => {
        socketRef.current = null
        if (closedRef.current) return
        if (stopRef.current) {
          setStatus("closed")
          return
        }
        scheduleReconnect()
      }

      socket.onerror = () => {
        socket.close()
      }
    }

    const scheduleReconnect = () => {
      if (closedRef.current || stopRef.current) return
      setStatus("reconnecting")
      const delay =
        BACKOFF_MS[Math.min(attemptRef.current, BACKOFF_MS.length - 1)]
      attemptRef.current += 1
      timerRef.current = setTimeout(connect, delay)
    }

    connect()

    // 回到前景時立即重試一次，不必等退避計時器。
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return
      if (closedRef.current || stopRef.current) return
      if (socketRef.current) return
      if (timerRef.current) clearTimeout(timerRef.current)
      attemptRef.current = 0
      connect()
    }
    document.addEventListener("visibilitychange", onVisibility)

    return () => {
      closedRef.current = true
      document.removeEventListener("visibilitychange", onVisibility)
      if (timerRef.current) clearTimeout(timerRef.current)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [taskId, handleMessage])

  const send = React.useCallback(
    (cmd: Omit<ClientCommand, "task_id">): boolean => {
      const socket = socketRef.current
      if (!socket || socket.readyState !== WebSocket.OPEN || !taskId)
        return false
      // task_id 必填，否則後端回 task_id_mismatch。
      const full: ClientCommand = { ...cmd, task_id: taskId }
      socket.send(JSON.stringify(full))
      return true
    },
    [taskId]
  )

  const clearLogs = React.useCallback(() => {
    seenRef.current = new Set()
    setEntries([])
  }, [])

  return {
    status,
    entries,
    clock,
    clockReceivedAt,
    screenshots,
    currentState,
    visitedStates,
    snapshotStatus,
    send,
    clearLogs,
  }
}
