import { ApiError, isSqlRejected } from '../api/client'
import type { ChatAnswer, ChatCell } from '../api/types'
import { NO_VALUE } from '../lib/format'

/**
 * One question and its reply. A *turn*, not two loose messages, because the API
 * is single-turn: each question is answered independently, so a question and
 * its answer belong together and nothing else can come between them.
 *
 * The union is discriminated on `status` so the renderer cannot read `answer`
 * off a turn that failed — the compiler rules out the state that would produce
 * an empty reply bubble.
 */
export type ChatTurn = { id: number; question: string } & (
  | { status: 'pending' }
  | { status: 'answered'; answer: ChatAnswer }
  | { status: 'failed'; error: unknown }
)

/** Shown when the SQL ran but the summarising LLM call failed (answer = null). */
const NO_PROSE_FALLBACK = "Here's what I found."

/**
 * A result cell, rendered without locale formatting.
 *
 * Deliberately not `formatInt`/`formatMoney` like the dashboard tables: those
 * know what their column *means*. Here the SQL is written per question, so a
 * number could be a case id, a dollar amount or a percentage, and thousands
 * separators on `case_id 7001` would be actively wrong. Numbers are printed as
 * the API sent them and right-aligned so they still line up.
 */
function formatCell(value: ChatCell): string {
  if (value === null) return NO_VALUE
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function ResultTable({ answer }: { answer: ChatAnswer }) {
  if (answer.row_count === 0) {
    return <p className="state">The query returned no rows.</p>
  }
  return (
    <>
      <div className="table-wrap chat__table">
        <table className="table">
          <thead>
            <tr>
              {answer.columns.map((column) => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {answer.rows.map((row, rowIndex) => (
              // The index is the key because an ad-hoc result has no id column
              // to rely on, and these rows are never reordered or mutated.
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td
                    key={answer.columns[cellIndex] ?? cellIndex}
                    className={typeof cell === 'number' ? 'num' : undefined}
                  >
                    {formatCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="chat__meta">
        {answer.row_count} {answer.row_count === 1 ? 'row' : 'rows'}
        {answer.truncated && ' (capped at 100 — there may be more)'} · {answer.model}
      </p>
    </>
  )
}

/**
 * The generated SQL, collapsed. Shown for every answer, success or rejection:
 * "what did it actually run?" is the first question anyone asks of a
 * text-to-SQL feature, and being able to check it is what makes the answer
 * trustworthy. `<details>` because most replies are read without it.
 */
function SqlDetails({ sql, label = 'View SQL' }: { sql: string; label?: string }) {
  return (
    <details className="sql">
      <summary>{label}</summary>
      <pre className="code">{sql}</pre>
    </details>
  )
}

/**
 * Failures rendered in the thread rather than as a page-level banner: the
 * question they belong to is right above them, and the next question should
 * still be askable.
 */
function TurnError({ error }: { error: unknown }) {
  // The server's 422 body carries the SQL it tried plus the validator's or the
  // warehouse's complaint. Showing both is the difference between "it didn't
  // work" and a question the user can fix.
  if (isSqlRejected(error)) {
    return (
      <>
        <p className="chat__prose">{error.message}</p>
        <p className="chat__error-detail">{error.detail.error}</p>
        <SqlDetails sql={error.detail.sql} label="View the SQL it tried" />
      </>
    )
  }

  // Two different things answer 503 here, and they need different instructions:
  // no API key, or no dbt artifacts (the schema briefing the model writes SQL
  // against is built from them, so the endpoint hits the same wall the catalog
  // pages do). The API's own detail says which, so branch on that rather than
  // on the status code alone.
  if (error instanceof ApiError && error.status === 503) {
    if (!error.message.includes('GROQ_API_KEY')) {
      return (
        <div className="empty">
          <p className="empty__title">No dbt artifacts yet</p>
          <p className="empty__body">
            The question is answered against a schema built from the files dbt writes to{' '}
            <code>anz_banking/target/</code>. Generate them from the repo root:
          </p>
          <pre className="code code--inline">make local-build && make local-docs</pre>
          <p className="empty__detail">{error.message}</p>
        </div>
      )
    }
    // Not an error so much as an unconfigured feature — the same treatment
    // QueryState gives "dbt hasn't run yet".
    return (
      <div className="empty">
        <p className="empty__title">AI chat needs a free API key</p>
        <p className="empty__body">
          The SQL is written by an LLM served through Groq's free tier. Create a key at{' '}
          <a href="https://console.groq.com" target="_blank" rel="noreferrer">
            console.groq.com
          </a>
          , add it to the repo's <code>.env</code>, and restart the API:
        </p>
        <pre className="code code--inline">GROQ_API_KEY=gsk_your_key_here</pre>
        <pre className="code code--inline">docker compose up -d api</pre>
        <p className="empty__detail">{error.message}</p>
      </div>
    )
  }

  if (error instanceof ApiError && error.status === 429) {
    return (
      <p className="chat__prose">
        The free tier is rate limited and this question was throttled. Try again in a moment.
      </p>
    )
  }

  return (
    <p className="chat__prose chat__prose--error" role="alert">
      {error instanceof Error ? error.message : 'Something went wrong asking that question.'}
    </p>
  )
}

function Reply({ turn }: { turn: ChatTurn }) {
  if (turn.status === 'pending') {
    return <p className="chat__prose chat__prose--muted">Writing SQL and running it…</p>
  }
  if (turn.status === 'failed') {
    return <TurnError error={turn.error} />
  }
  return (
    <>
      <p className="chat__prose">{turn.answer.answer ?? NO_PROSE_FALLBACK}</p>
      <ResultTable answer={turn.answer} />
      <SqlDetails sql={turn.answer.sql} />
    </>
  )
}

/** The transcript: each turn is the question on the right, the reply on the left. */
export function ChatThread({ turns }: { turns: ChatTurn[] }) {
  return (
    // aria-live so a screen reader announces the reply when it lands — the
    // answer arrives without any focus change to signal it.
    <ol className="chat__thread" aria-live="polite">
      {turns.map((turn) => (
        <li key={turn.id} className="chat__turn">
          <div className="chat__bubble chat__bubble--user">{turn.question}</div>
          <div className="chat__bubble chat__bubble--assistant">
            <Reply turn={turn} />
          </div>
        </li>
      ))}
    </ol>
  )
}
