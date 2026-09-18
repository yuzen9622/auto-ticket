import { apiFetch } from "./client"
import type { ExperimentDetailResponse, ExperimentListResponse } from "./types"

export function listExperiments(
  params: {
    task_id?: string
    limit?: number
    offset?: number
  } = {}
): Promise<ExperimentListResponse> {
  return apiFetch<ExperimentListResponse>("/api/v1/experiments", {
    query: {
      task_id: params.task_id,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
}

export function getExperiment(
  experimentId: string
): Promise<ExperimentDetailResponse> {
  return apiFetch<ExperimentDetailResponse>(
    `/api/v1/experiments/${encodeURIComponent(experimentId)}`
  )
}
