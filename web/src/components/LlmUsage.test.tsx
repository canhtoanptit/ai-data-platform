import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { LlmObservability } from '../api/types'
import { LlmUsage } from './LlmUsage'

/**
 * The AI usage panel, rendered from a real /api/observability/llm payload.
 *
 * Presentational component, so no fetching to stub — same shape as
 * RunSummary.test.tsx. The interesting assertions are the ones about *not*
 * lying: an unknown token count must not render as 0, and a 429 must not be
 * painted like a failure.
 */

const USAGE: LlmObservability = {
  today: { calls: 3, tokens: 4_120, budget: 200_000, budget_used_pct: 2.1 },
  recent: [
    {
      ts: '2026-08-31T09:15:04.120000+00:00',
      question: 'Which team has the highest cure rate?',
      source: 'chat',
      model: 'llama-3.3-70b-versatile',
      guard_ok: true,
      row_count: 1,
      tokens: 1_402,
      latency_ms_total: 1_840,
      http_status: 200,
      error_class: null,
    },
    {
      ts: '2026-08-31T09:12:44.000000+00:00',
      question: 'How many collection cases are currently open?',
      source: 'eval',
      model: 'llama-3.3-70b-versatile',
      guard_ok: true,
      row_count: 1,
      tokens: 980,
      latency_ms_total: 620,
      http_status: 200,
      error_class: null,
    },
    {
      ts: '2026-08-31T09:10:01.000000+00:00',
      question: 'drop everything',
      source: 'chat',
      model: 'llama-3.3-70b-versatile',
      guard_ok: false,
      row_count: null,
      tokens: null,
      latency_ms_total: 2_100,
      http_status: 422,
      error_class: 'UnsafeSql',
    },
  ],
}

describe('LlmUsage', () => {
  it('shows the day-so-far counts and the budget as a fraction', () => {
    render(<LlmUsage usage={USAGE} />)

    expect(screen.getByText('Calls today')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('4,120')).toBeInTheDocument()
    expect(screen.getByText('4,120 / 200,000 tokens')).toBeInTheDocument()
    // The server's percentage, verbatim — the component must not recompute the
    // label from the clamped bar width.
    expect(screen.getByText(/^2\.1% of today/)).toBeInTheDocument()
  })

  it('draws the budget bar at the used fraction', () => {
    render(<LlmUsage usage={USAGE} />)

    const bar = screen.getByRole('progressbar', { name: /token budget/i })
    // 4120/200000 = 2.06%, rounded for the aria value.
    expect(bar).toHaveAttribute('aria-valuenow', '2')
    const fill = bar.querySelector('.budget__fill') as HTMLElement
    expect(fill).toHaveClass('budget__fill--ok')
    expect(Number.parseFloat(fill.style.width)).toBeCloseTo(2.06)
  })

  it('turns the bar red and explains itself when the budget is spent', () => {
    render(
      <LlmUsage
        usage={{ ...USAGE, today: { ...USAGE.today, tokens: 210_000, budget_used_pct: 105 } }}
      />,
    )

    const fill = screen.getByRole('progressbar').querySelector('.budget__fill') as HTMLElement
    expect(fill).toHaveClass('budget__fill--spent')
    // Clamped to the track even though the label reads over 100%.
    expect(fill.style.width).toBe('100%')
    expect(screen.getByText(/answers 429 until midnight UTC/)).toBeInTheDocument()
    // The label still tells the truth: over budget, not pinned at 100%.
    expect(screen.getByText(/^105% of today/)).toBeInTheDocument()
  })

  it('lists the recent calls with their status and cost', () => {
    render(<LlmUsage usage={USAGE} />)

    expect(screen.getAllByRole('row')).toHaveLength(4) // header + 3 calls
    expect(screen.getByText('Which team has the highest cure rate?')).toBeInTheDocument()
    expect(screen.getByText('1,402')).toBeInTheDocument()
    // Latency crosses the second boundary in both directions.
    expect(screen.getByText('1.8s')).toBeInTheDocument()
    expect(screen.getByText('620ms')).toBeInTheDocument()
  })

  it('labels eval traffic and leaves live traffic unlabelled', () => {
    render(<LlmUsage usage={USAGE} />)
    expect(screen.getByText('eval')).toBeInTheDocument()
    expect(screen.queryByText('chat')).not.toBeInTheDocument()
  })

  it('renders a rejected call as an error and an unknown cost as a dash', () => {
    render(<LlmUsage usage={USAGE} />)

    const badge = screen.getByText('UnsafeSql')
    expect(badge).toHaveClass('badge--bad')
    // Null tokens must not render as a confident 0 — that would be a claim about
    // a call that never reported its usage.
    expect(screen.getByText('–')).toBeInTheDocument()
  })

  it('paints a 429 as amber, not red: the system worked', () => {
    render(
      <LlmUsage
        usage={{
          ...USAGE,
          recent: [
            {
              ...USAGE.recent[0],
              http_status: 429,
              error_class: 'BudgetExhausted',
              tokens: null,
            },
          ],
        }}
      />,
    )
    expect(screen.getByText('BudgetExhausted')).toHaveClass('badge--warn')
  })

  it('says so plainly when nothing has been traced yet', () => {
    render(<LlmUsage usage={{ ...USAGE, recent: [] }} />)

    expect(screen.getByText(/No LLM calls traced yet/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
