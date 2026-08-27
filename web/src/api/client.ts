/**
 * Typed fetch functions over the FastAPI read layer.
 *
 * Every URL is relative (`/api/...`), never absolute: in dev the Vite proxy
 * forwards it to localhost:8000, in the container nginx forwards it to the `api`
 * service. Nothing here needs to know which, so there is no base URL to
 * configure and no environment variable to get wrong.
 */

import type { Case, CaseStatus, Health, MetricsSummary, PerformanceRow } from './types'

/** Thrown for any non-2xx response, so react-query surfaces it as `error`. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, statusText: string, path: string) {
    super(`GET ${path} failed: ${status} ${statusText}`)
    this.name = 'ApiError'
    this.status = status
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText, path)
  }
  return (await response.json()) as T
}

/** Liveness plus a warehouse round-trip. Answers 200 even when the db is down. */
export function getHealth(): Promise<Health> {
  return getJson<Health>('/api/health')
}

export function getSummary(): Promise<MetricsSummary> {
  return getJson<MetricsSummary>('/api/metrics/summary')
}

export function getPerformance(): Promise<PerformanceRow[]> {
  return getJson<PerformanceRow[]>('/api/metrics/performance')
}

export interface CaseQuery {
  status?: CaseStatus
  bucket?: string
  limit?: number
  offset?: number
}

export function getCases(params: CaseQuery = {}): Promise<Case[]> {
  const search = new URLSearchParams()
  // Omit rather than send an empty value: the API treats a missing param as
  // "no filter", but `?status=` would be a filter on the empty string.
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const query = search.size > 0 ? `?${search}` : ''
  return getJson<Case[]>(`/api/cases${query}`)
}
