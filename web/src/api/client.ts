/**
 * Typed fetch functions over the FastAPI read layer.
 *
 * Every URL is relative (`/api/...`), never absolute: in dev the Vite proxy
 * forwards it to localhost:8000, in the container nginx forwards it to the `api`
 * service. Nothing here needs to know which, so there is no base URL to
 * configure and no environment variable to get wrong.
 */

import type {
  Case,
  CaseStatus,
  Health,
  LatestRun,
  Lineage,
  MetricsSummary,
  ModelDetail,
  ModelSummary,
  PerformanceRow,
} from './types'

/** Thrown for any non-2xx response, so react-query surfaces it as `error`. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * FastAPI puts its own explanation in `{"detail": "..."}` — for a 503 that is
 * "run `make local-build && make local-docs`", which is far more useful on
 * screen than "Service Unavailable". Falls back to the status line when the
 * body is not the JSON we expect (a proxy error page, say).
 */
async function errorMessage(response: Response, path: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // Not JSON. Nothing to add.
  }
  return `GET ${path} failed: ${response.status} ${response.statusText}`
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response, path))
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

/* --- dbt metadata ------------------------------------------------------------
 *
 * These four read dbt's build artifacts rather than the warehouse, so they
 * answer 503 (not 500) until `make local-build && make local-docs` has run. The
 * ApiError carries the API's own instructions as its message; QueryState turns
 * a 503 into the empty state that shows them.
 */

/** Every model, seed and snapshot, ordered by pipeline layer then name. */
export function getCatalogModels(): Promise<ModelSummary[]> {
  return getJson<ModelSummary[]>('/api/catalog/models')
}

/** One node with its columns, tests, neighbours and SQL. 404 if unknown. */
export function getCatalogModel(name: string): Promise<ModelDetail> {
  return getJson<ModelDetail>(`/api/catalog/models/${encodeURIComponent(name)}`)
}

/** The whole DAG: sources, seeds, models and snapshots, plus their edges. */
export function getLineage(): Promise<Lineage> {
  return getJson<Lineage>('/api/catalog/lineage')
}

/** Status and timings of the last `dbt build`. */
export function getLatestRun(): Promise<LatestRun> {
  return getJson<LatestRun>('/api/runs/latest')
}
