import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import type { ChatAnswer } from '../api/types'
import type { ChatTurn } from './ChatThread'
import { ChatThread } from './ChatThread'

/** A real /api/chat 200 payload for the seeded dataset. */
const ANSWER: ChatAnswer = {
  question: 'Which team has the highest cure rate?',
  sql: 'SELECT\n  team,\n  cure_rate_pct\nFROM analytics_marts.collections_performance\nLIMIT 100',
  columns: ['team', 'cure_rate_pct'],
  rows: [
    ['early_stage', 100],
    ['late_stage', 0],
    [null, 25.5],
  ],
  row_count: 3,
  truncated: false,
  answer: 'The early_stage team has the highest cure rate at 100%.',
  model: 'llama-3.3-70b-versatile',
}

const answered = (answer: ChatAnswer = ANSWER): ChatTurn => ({
  id: 1,
  question: answer.question,
  status: 'answered',
  answer,
})

describe('ChatThread', () => {
  it('renders the question, the prose answer, the rows and the SQL', () => {
    render(<ChatThread turns={[answered()]} />)

    expect(screen.getByText('Which team has the highest cure rate?')).toBeInTheDocument()
    expect(screen.getByText(/highest cure rate at 100%/)).toBeInTheDocument()

    // The result table: headers from `columns`, one row per result row.
    expect(screen.getByRole('columnheader', { name: 'team' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'cure_rate_pct' })).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(4) // header + 3
    expect(screen.getByText('early_stage')).toBeInTheDocument()
    // A null cell is a dash, never blank and never 0.
    expect(screen.getByText('–')).toBeInTheDocument()

    // The SQL is present but collapsed behind a <details>.
    const details = screen.getByText('View SQL').closest('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
    expect(screen.getByText(/FROM analytics_marts.collections_performance/)).toBeInTheDocument()

    expect(screen.getByText(/3 rows/)).toBeInTheDocument()
    expect(screen.getByText(/llama-3\.3-70b-versatile/)).toBeInTheDocument()
  })

  // The summarising LLM call is allowed to fail without losing the query result
  // — the rows are the answer, the sentence is a nicety.
  it('falls back to a stock line when the API returned no prose', () => {
    render(<ChatThread turns={[answered({ ...ANSWER, answer: null })]} />)

    expect(screen.getByText("Here's what I found.")).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('says so when the query returned no rows', () => {
    render(
      <ChatThread turns={[answered({ ...ANSWER, rows: [], row_count: 0, answer: null })]} />,
    )

    expect(screen.getByText('The query returned no rows.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('flags a truncated result', () => {
    render(<ChatThread turns={[answered({ ...ANSWER, truncated: true, row_count: 100 })]} />)

    expect(screen.getByText(/capped at 100/)).toBeInTheDocument()
  })

  it('shows a pending turn without an answer', () => {
    render(<ChatThread turns={[{ id: 1, question: 'anything?', status: 'pending' }]} />)

    expect(screen.getByText(/Writing SQL/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  // The 503 is the state the repo ships in: no GROQ_API_KEY on the server. It
  // must read as setup instructions, not as a crash.
  it('renders the 503 as a setup state with the key name and the console link', () => {
    const error = new ApiError(
      503,
      'AI chat is not configured: this server has no GROQ_API_KEY. Get a free key at https://console.groq.com',
    )
    render(<ChatThread turns={[{ id: 1, question: 'anything?', status: 'failed', error }]} />)

    expect(screen.getByText('AI chat needs a free API key')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'console.groq.com' })).toHaveAttribute(
      'href',
      'https://console.groq.com',
    )
    expect(screen.getByText('GROQ_API_KEY=gsk_your_key_here')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // The other 503: the key is set but dbt hasn't run, so there is no schema to
  // brief the model with. Same status, different fix.
  it('renders a 503 about missing dbt artifacts as build instructions', () => {
    const error = new ApiError(503, 'manifest.json is unavailable (not found at /srv/dbt-target)')
    render(<ChatThread turns={[{ id: 1, question: 'anything?', status: 'failed', error }]} />)

    expect(screen.getByText('No dbt artifacts yet')).toBeInTheDocument()
    expect(screen.getByText('make local-build && make local-docs')).toBeInTheDocument()
    expect(screen.queryByText('AI chat needs a free API key')).not.toBeInTheDocument()
  })

  it('renders a 429 as a retry hint', () => {
    const error = new ApiError(429, 'free tier is rate limited, retry shortly')
    render(<ChatThread turns={[{ id: 1, question: 'anything?', status: 'failed', error }]} />)

    expect(screen.getByText(/rate limited/)).toBeInTheDocument()
    expect(screen.getByText(/Try again in a moment/)).toBeInTheDocument()
  })

  // A 422 means the model wrote SQL the guard or the warehouse refused. Showing
  // the attempt and the reason is what makes it debuggable.
  it('renders a 422 with the attempted SQL and the validator message', () => {
    const error = new ApiError(422, 'The generated SQL was rejected by the warehouse.', {
      message: 'The generated SQL was rejected by the warehouse.',
      sql: 'SELECT nope FROM analytics_marts.dim_agents LIMIT 100',
      error: 'column "nope" does not exist',
    })
    render(<ChatThread turns={[{ id: 1, question: 'anything?', status: 'failed', error }]} />)

    expect(screen.getByText('The generated SQL was rejected by the warehouse.')).toBeInTheDocument()
    expect(screen.getByText('column "nope" does not exist')).toBeInTheDocument()
    expect(screen.getByText('View the SQL it tried')).toBeInTheDocument()
    expect(screen.getByText(/SELECT nope FROM/)).toBeInTheDocument()
  })

  it('renders several turns in order', () => {
    render(
      <ChatThread
        turns={[
          answered(),
          { id: 2, question: 'and the worst?', status: 'pending' },
        ]}
      />,
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Which team has the highest cure rate?')
    expect(items[1]).toHaveTextContent('and the worst?')
  })
})
