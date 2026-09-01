/**
 * Mirrors `api/app/schemas.py` and `api/app/schemas_catalog.py`. Kept
 * hand-written rather than generated from the OpenAPI schema: the surface is a
 * handful of endpoints, and a hand-written file is one less build step to
 * explain. If the API grows, generate it.
 *
 * Two conventions carried over from the Pydantic models:
 *  - money and rates are `number` (the API serialises them as JSON numbers, not
 *    Decimal strings, because they are display figures);
 *  - a rate is `null`, never 0, when its denominator is empty — "unknown" and
 *    "zero percent" are different answers, and the UI renders them differently.
 */

export interface Health {
  status: string
  /** 'ok' or 'fail' — whether the warehouse answered `select 1`. */
  database: string
  detail: string | null
}

export interface MetricsSummary {
  total_cases: number
  open_cases: number
  total_delinquent_amount: number
  cure_rate_pct: number | null
  ptp_kept_rate_pct: number | null
  rpc_rate_pct: number | null
}

/** One row of the `collections_performance` mart: team x delinquency bucket. */
export interface PerformanceRow {
  team: string | null
  delinquency_bucket: string
  case_count: number
  delinquent_amount: number
  cured_cases: number
  written_off_cases: number
  cure_rate_pct: number | null
  ptp_kept_rate_pct: number | null
  rpc_rate_pct: number | null
}

export type CaseStatus = 'open' | 'resolved' | 'written_off'

/** One row of `fct_collection_cases`. Dates arrive as ISO `YYYY-MM-DD` strings. */
export interface Case {
  case_id: number
  account_id: number
  customer_id: number
  customer_name: string | null
  product_type: string | null
  agent_id: number | null
  opened_date: string
  resolved_date: string | null
  days_past_due: number
  delinquency_bucket: string
  delinquent_amount: number
  case_status: string
  is_cured: number
  is_written_off: number
  contact_attempts: number
  rpc_count: number
  ptp_count: number
  ptp_kept_count: number
}

/* --- dbt metadata (api/app/schemas_catalog.py) -------------------------------
 *
 * These come from dbt's build artifacts, not from the warehouse: manifest.json
 * (the DAG, docs, tests), catalog.json (warehouse column types) and
 * run_results.json (last run status). Every endpoint below answers 503 when the
 * artifacts are missing — see ApiError in client.ts.
 */

/**
 * Where a node sits in the pipeline. `staging | intermediate | marts` come from
 * the model's folder; the rest from its dbt resource type. `unknown` is what a
 * model in some other folder gets — it stays visible rather than vanishing.
 */
export type Layer =
  | 'source'
  | 'seed'
  | 'staging'
  | 'intermediate'
  | 'marts'
  | 'snapshot'
  | 'unknown'

/** A row in the catalog listing: one dbt model, seed or snapshot. */
export interface ModelSummary {
  unique_id: string
  name: string
  resource_type: string
  layer: Layer
  schema: string
  materialization: string
  /** Empty string when the model has no `description:` in its .yml. */
  description: string
  column_count: number
  test_count: number
}

/** One column, merged from the manifest (docs, tests) and catalog (type). */
export interface NodeColumn {
  name: string
  /** null until `dbt docs generate` has run, and for ephemeral models. */
  data_type: string | null
  description: string | null
  tests: string[]
}

export interface ModelDetail extends ModelSummary {
  columns: NodeColumn[]
  /** Tests on the model as a whole rather than on one column. */
  table_tests: string[]
  depends_on: string[]
  referenced_by: string[]
  raw_sql: string | null
  /** null for seeds (a CSV has no SQL) and for models that never compiled. */
  compiled_sql: string | null
}

export interface LineageNode {
  /** dbt unique_id, e.g. `model.anz_banking.fct_collection_cases`. */
  id: string
  name: string
  resource_type: string
  layer: Layer
}

export interface LineageEdge {
  source: string
  target: string
}

export interface Lineage {
  nodes: LineageNode[]
  edges: LineageEdge[]
}

/**
 * dbt statuses are two enums, not one: models/seeds/snapshots report
 * success/error/skipped, tests report pass/fail/warn. Both are tallied here so
 * the UI can say "21 built, 39 passed" without knowing which is which.
 */
export interface RunCounts {
  success: number
  error: number
  skipped: number
  pass: number
  fail: number
  warn: number
}

export interface RunResultRow {
  unique_id: string
  name: string
  resource_type: string
  status: string
  /** Seconds. */
  execution_time: number
  message: string | null
}

export interface LatestRun {
  /** ISO 8601 UTC, when dbt wrote run_results.json. */
  generated_at: string
  elapsed_total: number
  counts: RunCounts
  results: RunResultRow[]
}

/* --- AI chat (api/app/schemas_chat.py) ---------------------------------------
 *
 * The one endpoint whose result shape is not known ahead of time: the SQL is
 * written per question, so the columns are data rather than a type.
 */

/** A single result cell. Dates arrive as ISO strings, numerics as numbers. */
export type ChatCell = string | number | boolean | null

export interface ChatAnswer {
  question: string
  /** The validated, row-limited SQL that actually ran. Always present. */
  sql: string
  columns: string[]
  /** Positional rows matching `columns`. */
  rows: ChatCell[][]
  row_count: number
  /** True when the 100-row cap was hit, so there may be more. */
  truncated: boolean
  /** null when the summarising LLM call failed — the rows are still the answer. */
  answer: string | null
  /** Which LLM wrote the SQL, e.g. `llama-3.3-70b-versatile`. */
  model: string
}

/**
 * The 422 body: the model produced SQL that the guard or the warehouse refused.
 * Carries the attempt so the UI can show what it tried instead of a dead end.
 */
export interface ChatSqlRejected {
  message: string
  sql: string
  error: string
}

/* --- LLM observability (api/app/schemas_ops.py) -------------------------------
 *
 * The API's own operational data, not the warehouse's: every /api/chat request
 * writes a row to `platform_ops.llm_calls`, and this is that table aggregated.
 * The daily token budget is enforced off the same rows, so the number shown here
 * is the number that stops a request.
 */

export interface LlmUsageToday {
  calls: number
  /** prompt + completion, summed across today's (UTC) calls. */
  tokens: number
  /** LLM_DAILY_TOKEN_BUDGET on the server. */
  budget: number
  /** Computed server-side so no two clients can divide it differently. */
  budget_used_pct: number
}

export interface LlmCallRow {
  /** ISO 8601 UTC. */
  ts: string
  /** Truncated by the API — this is an operations table, not a transcript. */
  question: string
  /** `chat` for a real request, `eval` for the harness (`make eval`). */
  source: string
  model: string
  /** null when the request never reached the SQL guard (budget, config). */
  guard_ok: boolean | null
  row_count: number | null
  /** null, not 0, when the provider reported no usage. */
  tokens: number | null
  latency_ms_total: number
  http_status: number
  /** e.g. `UnsafeSql`, `BudgetExhausted`, `LlmRateLimited`. null on success. */
  error_class: string | null
}

export interface LlmObservability {
  today: LlmUsageToday
  /** Most recent first, capped at 20 by the API. */
  recent: LlmCallRow[]
}
