/**
 * Mirrors `api/app/schemas.py`. Kept hand-written rather than generated from the
 * OpenAPI schema: the surface is five endpoints, and a hand-written file is one
 * less build step to explain. If the API grows, generate it.
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
