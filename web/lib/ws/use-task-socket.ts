"use client"

import * as React from "react"

import { wsBaseUrl } from "@/lib/config"
import { isTaskFinished } from "@/lib/fsm"
import type { WsStatus } from "@/lib/task-status"
import type {
  ClientCommand,
  ClockTickPayload,
  ServerMessage,
  CloudflareGraceLogPayload,
  OcrProgressLogPayload,
  VerificationDoneLogPayload,
  HumanGateLogPayload,
} from "@/lib/ws/types"
import {
  isHumanGate,
  isSnapshot,
  isCloudflareGrace,
  isOcrProgress,
  isVerificationDone,
} from "@/lib/ws/types"

export type AutomationStatus =
  | CloudflareGraceLogPayload
  | OcrProgressLogPayload
  | VerificationDoneLogPayload

/** 指數退避：500ms → 1s → 2s → 4s → 8s（上限）。 */
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000]

export interface TaskSocketState {
  status: WsStatus
  clock: ClockTickPayload | null
  clockReceivedAt: number
  currentState: string | null
  visitedStates: string[]
  /** Worker 正在等人處理；狀態一往前走就清掉。 */
  humanGate: HumanGateLogPayload | null
  /** 自動化執行狀態（Cloudflare 寬限、OCR 辨識進度、完成）。 */
  automation: AutomationStatus | null
  send: (cmd: Omit<ClientCommand, "task_id">) => boolean
}

export function useTaskSocket(
  taskId: string | null,
  options: { taskStatus?: string | null } = {}
): TaskSocketState {
  const { taskStatus } = options

  const [status, setStatus] = React.useState<WsStatus>("connecting")
  const [clock, setClock] = React.useState<ClockTickPayload | null>(null)
  // 收到這則時鐘訊息的本地時刻；倒數靠它補兩則訊息之間的秒數。
  const [clockReceivedAt, setClockReceivedAt] = React.useState(0)
  const [currentState, setCurrentState] = React.useState<string | null>(null)
  const [visitedStates, setVisitedStates] = React.useState<string[]>([])
  const [humanGate, setHumanGate] =
    React.useState<HumanGateLogPayload | null>(null)
  const [automation, setAutomation] =
    React.useState<AutomationStatus | null>(null)

  const socketRef = React.useRef<WebSocket | null>(null)
  const attemptRef = React.useRef(0)
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const closedRef = React.useRef(false)

  const isFinished = Boolean(taskStatus && isTaskFinished(taskStatus))
  const stopRef = React.useRef(isFinished)
  React.useEffect(() => {
    stopRef.current = isFinished
  }, [isFinished])

  const handleMessage = React.useCallback((msg: ServerMessage) => {
    if (msg.type === "CLOCK_TICK") {
      setClock(msg.payload)
      setClockReceivedAt(Date.now())
    }

    if (msg.type === "STATE_CHANGED") {
      const { to_state } = msg.payload
      // 狀態往前走＝閘門過了，等人的提示就該收掉。
      setHumanGate(null)
      setAutomation(null)
      setCurrentState(to_state)
      setVisitedStates((prev) =>
        prev.includes(to_state) ? prev : [...prev, to_state]
      )
    }

    if (msg.type === "TASK_LOG" && isHumanGate(msg.payload)) {
      setHumanGate(msg.payload)
      setAutomation(null)
    }

    if (
      msg.type === "TASK_LOG" &&
      (isCloudflareGrace(msg.payload) ||
        isOcrProgress(msg.payload) ||
        isVerificationDone(msg.payload))
    ) {
      setAutomation(msg.payload)
    }

    // 重連後的初始快照代表流程重新對齊，先前的自動化提示不再成立。
    if (msg.type === "TASK_LOG" && isSnapshot(msg.payload)) {
      setAutomation(null)
    }
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

  return {
    status,
    clock,
    clockReceivedAt,
    currentState,
    visitedStates,
    humanGate,
    automation,
    send,
  }
}
