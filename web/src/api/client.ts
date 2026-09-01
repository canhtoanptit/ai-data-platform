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
  ChatAnswer,
  ChatSqlRejected,
  Health,
  LatestRun,
  Lineage,
  LlmObservability,
  MetricsSummary,
  ModelDetail,
  ModelSummary,
  PerformanceRow,
} from './types'

/** Thrown for any non-2xx response, so react-query surfaces it as `error`. */
export class ApiError extends Error {
  readonly status: number
  /**
   * The raw `detail` from the body, when it is not a plain string. `/api/chat`
   * answers 422 with `{message, sql, error}` so the UI can show the SQL the
   * model tried; `message` becomes the Error's message and this keeps the rest.
   */
  readonly detail: unknown

  constructor(status: number, message: string, detail: unknown = undefined) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Narrows an ApiError's `detail` to the chat 422 shape. */
export function isSqlRejected(error: unknown): error is ApiError & { detail: ChatSqlRejected } {
  if (!(error instanceof ApiError)) return false
  const detail = error.detail as Partial<ChatSqlRejected> | undefined
  return typeof detail?.sql === 'string' && typeof detail?.error === 'string'
}

/**
 * Which kind of 429 this is. Both are *expected states* rather than faults, but
 * they need different words: the budget is gone until midnight UTC and the user
 * can do nothing about it, while a rate limit clears in under a minute.
 *
 * Matched on the message text because the API answers both with a plain
 * `{"detail": "..."}` and one status code. A typed `code` field in the body
 * would be sturdier; this stays in one place so there is one thing to change if
 * the API ever grows one.
 */
export type ThrottleKind = 'budget' | 'rate-limit' | 'provider'

export function throttleKind(error: ApiError): ThrottleKind {
  if (error.message.includes('token budget')) return 'budget'
  if (error.message.includes('Too many questions')) return 'rate-limit'
  return 'provider'
}

/**
 * FastAPI puts its own explanation in `{"detail": "..."}` — for a 503 that is
 * "run `make local-build && make local-docs`", which is far more useful on
 * screen than "Service Unavailable". Falls back to the status line when the
 * body is not the JSON we expect (a proxy error page, say).
 */
async function apiError(response: Response, method: string, path: string): Promise<ApiError> {
  const fallback = `${method} ${path} failed: ${response.status} ${response.statusText}`
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body.detail
    if (typeof detail === 'string') return new ApiError(response.status, detail)
    // A structured detail (the chat 422). Keep the object for the caller and
    // lift its `message` so generic error rendering still says something useful.
    if (detail !== null && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      return new ApiError(
        response.status,
        typeof message === 'string' ? message : fallback,
        detail,
      )
    }
  } catch {
    // Not JSON. Nothing to add.
  }
  return new ApiError(response.status, fallback)
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw await apiError(response, 'GET', path)
  }
  return (await response.json()) as T
}

/** The only write in the client: `/api/chat` takes the question in the body. */
async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw await apiError(response, 'POST', path)
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

/**
 * Ask a natural-language question about the marts. The API writes the SQL with
 * an LLM, validates it, runs it read-only and returns SQL + rows + prose.
 *
 * Four failure codes worth handling, all of them via ApiError.status:
 * 503 (no GROQ_API_KEY on the server), 429 (free tier throttled), 422 (the
 * model's SQL was rejected — `detail` carries the attempt, see isSqlRejected),
 * 502 (the provider failed).
 */
export function askChat(question: string): Promise<ChatAnswer> {
  return postJson<ChatAnswer>('/api/chat', { question })
}

/**
 * Today's LLM spend against the budget, plus the last 20 traced calls.
 *
 * Always 200 on a running API — zeros and an empty list when nothing has been
 * asked yet. The Runs page therefore treats *any* failure here (an older API
 * without the endpoint, a proxy error) as "hide the section": an ops panel is
 * not worth an error banner on a page about dbt.
 */
export function getLlmObservability(): Promise<LlmObservability> {
  return getJson<LlmObservability>('/api/observability/llm')
}
