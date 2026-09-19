import { apiFetch } from "./client"
import type {
  CancelTaskResponse,
  CreateTaskRequest,
  StartTaskResponse,
  TaskDetailResponse,
  TaskListResponse,
  TaskResponse,
} from "./types"

export function createTask(body: CreateTaskRequest): Promise<TaskResponse> {
  return apiFetch<TaskResponse>("/api/v1/tasks", { method: "POST", body })
}

export function listTasks(
  params: {
    status?: string
    limit?: number
    offset?: number
  } = {}
): Promise<TaskListResponse> {
  return apiFetch<TaskListResponse>("/api/v1/tasks", {
    // router 以 alias="status" 接收（src/api/routers/tasks.py:95）
    query: {
      status: params.status,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
}

export function getTask(taskId: string): Promise<TaskDetailResponse> {
  return apiFetch<TaskDetailResponse>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}`
  )
}

export function startTask(taskId: string): Promise<StartTaskResponse> {
  return apiFetch<StartTaskResponse>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}/start`,
    { method: "POST" }
  )
}

export function cancelTask(taskId: string): Promise<CancelTaskResponse> {
  return apiFetch<CancelTaskResponse>(
    `/api/v1/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST" }
  )
}

export function deleteTask(taskId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  })
}

export function isStartable(status: string): boolean {
  return status === "SCHEDULED"
}

export function isCancellable(status: string): boolean {
  return !["COMPLETED", "FAILED", "CANCELLED"].includes(status)
}
