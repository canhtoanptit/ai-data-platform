import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { MetricsSummary } from '../api/types'
import { NO_VALUE } from '../lib/format'
import { KpiTiles } from './KpiTiles'

/** The real /api/metrics/summary payload for the seeded dataset. */
const SUMMARY: MetricsSummary = {
  total_cases: 8,
  open_cases: 4,
  total_delinquent_amount: 16630.75,
  cure_rate_pct: 25,
  ptp_kept_rate_pct: 50,
  rpc_rate_pct: 35.3,
}

describe('KpiTiles', () => {
  it('renders one tile per metric, formatted', () => {
    render(<KpiTiles summary={SUMMARY} />)

    expect(screen.getAllByRole('listitem')).toHaveLength(6)
    expect(screen.getByText('Total cases')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('$16.6k')).toBeInTheDocument()
    expect(screen.getByText('25.0%')).toBeInTheDocument()
    expect(screen.getByText('35.3%')).toBeInTheDocument()
  })

  // The API returns null for a rate whose denominator is empty (no promises to
  // pay yet, say). Rendering that as 0% would be a wrong answer, not a blank one.
  it('renders null rates as a dash rather than zero', () => {
    render(<KpiTiles summary={{ ...SUMMARY, ptp_kept_rate_pct: null }} />)

    expect(screen.getByText(NO_VALUE)).toBeInTheDocument()
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
  })
})
