import type { LlmCallRow, LlmObservability } from '../api/types'
import { budgetFillPct, budgetTone, formatTokenBudget } from '../lib/budget'
import { NO_VALUE, formatTimestamp } from '../lib/format'

/**
 * What the AI feature did today: spend against the budget, and the last calls.
 *
 * Presentational on purpose — no fetching here, so it is trivial to test and the
 * page owns the decision about whether to show it at all (see RunsPage).
 *
 * This sits on the Runs page rather than beside the chat because it belongs to
 * the same question that page already answers: *is the pipeline healthy, and
 * what did it cost?* dbt's run results and the LLM's token spend are two halves
 * of one operational view, and an operator checking one wants the other.
 */

/** The one status word per call, derived rather than stored. */
function callStatus(row: LlmCallRow): { label: string; tone: 'good' | 'bad' | 'warn' } {
  if (row.http_status === 200) return { label: 'ok', tone: 'good' }
  // 429 is not a failure of the system, it is the system working: the budget or
  // the rate limit refused a request on purpose. Amber, not red.
  if (row.http_status === 429) return { label: row.error_class ?? 'throttled', tone: 'warn' }
  return { label: row.error_class ?? String(row.http_status), tone: 'bad' }
}

/** `1234` -> `1.2s`, `840` -> `840ms`. Latencies here span both scales. */
function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

/** Time of day only: the whole table is the last few minutes to hours. */
function formatTime(ts: string): string {
  const parsed = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(ts) ? ts : `${ts}Z`)
  if (Number.isNaN(parsed.getTime())) return formatTimestamp(ts)
  return parsed.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

function BudgetBar({ tokens, budget, pct }: { tokens: number; budget: number; pct: number }) {
  const fill = budgetFillPct(tokens, budget)
  const tone = budgetTone(tokens, budget)
  return (
    <div className="budget">
      <div className="budget__labels">
        <span className="budget__amount">{formatTokenBudget(tokens, budget)}</span>
        {/* The server's percentage, not the clamped bar width — it is allowed to
            read over 100%, and hiding that would hide why requests are 429ing. */}
        <span className="budget__pct">{pct}% of today's budget</span>
      </div>
      <div
        className="budget__track"
        role="progressbar"
        aria-label="LLM token budget used today"
        aria-valuenow={Math.round(fill)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`budget__fill budget__fill--${tone}`} style={{ width: `${fill}%` }} />
      </div>
      {tone === 'spent' && (
        <p className="budget__note">
          The budget is spent — /api/chat answers 429 until midnight UTC. Raise
          <code> LLM_DAILY_TOKEN_BUDGET</code> to allow more.
        </p>
      )}
    </div>
  )
}

function CallsTable({ rows }: { rows: LlmCallRow[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Question</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Tokens
            </th>
            <th scope="col" className="num">
              Latency
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const status = callStatus(row)
            return (
              // ts + index as the key: the API does not send the row id, and two
              // calls can share a timestamp to the second.
              <tr key={`${row.ts}-${index}`}>
                <td>{formatTime(row.ts)}</td>
                <td>
                  {row.question}
                  {/* Only for eval traffic. Live traffic is the default and
                      labelling every row 'chat' would be noise. */}
                  {row.source !== 'chat' && <span className="chip">{row.source}</span>}
                </td>
                <td>
                  <span className={`badge badge--${status.tone}`}>{status.label}</span>
                </td>
                <td className="num">{row.tokens?.toLocaleString('en-US') ?? NO_VALUE}</td>
                <td className="num">{formatLatency(row.latency_ms_total)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function LlmUsage({ usage }: { usage: LlmObservability }) {
  const { today, recent } = usage
  return (
    <>
      <ul className="tiles">
        <li className="tile">
          <p className="tile__label">Calls today</p>
          <p className="tile__value">{today.calls.toLocaleString('en-US')}</p>
        </li>
        <li className="tile">
          <p className="tile__label">Tokens today</p>
          <p className="tile__value">{today.tokens.toLocaleString('en-US')}</p>
        </li>
      </ul>
      <BudgetBar tokens={today.tokens} budget={today.budget} pct={today.budget_used_pct} />
      {recent.length === 0 ? (
        <p className="state">No LLM calls traced yet. Ask something on the Ask AI page.</p>
      ) : (
        <CallsTable rows={recent} />
      )}
    </>
  )
}
