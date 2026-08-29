import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { LatestRun } from '../api/types'
import { RunSummary } from './RunSummary'

/** The real /api/runs/latest payload for a clean local build (results trimmed). */
const CLEAN: LatestRun = {
  generated_at: '2026-08-28T12:55:42.553494Z',
  elapsed_total: 1.2670488357543945,
  counts: { success: 21, error: 0, skipped: 0, pass: 39, fail: 0, warn: 0 },
  results: [],
}

describe('RunSummary', () => {
  it('renders a tile per figure, formatted', () => {
    render(<RunSummary run={CLEAN} />)

    expect(screen.getAllByRole('listitem')).toHaveLength(6)
    expect(screen.getByText('Models built')).toBeInTheDocument()
    expect(screen.getByText('21')).toBeInTheDocument()
    expect(screen.getByText('39')).toBeInTheDocument()
    // Seconds to two decimals, not the raw float.
    expect(screen.getByText('1.27s')).toBeInTheDocument()
  })

  it('humanizes the run timestamp', () => {
    render(<RunSummary run={CLEAN} />)
    // Rendered in the reader's timezone, so assert on the parts that survive
    // the shift rather than on a wall-clock time this suite cannot know.
    expect(screen.getByText(/2026/)).toBeInTheDocument()
    expect(screen.getByText(/Aug/)).toBeInTheDocument()
  })

  it('shows a clean run as zero failures, not as a blank', () => {
    render(<RunSummary run={CLEAN} />)
    const failures = screen.getByText('Build failures').parentElement
    expect(failures).toHaveTextContent('0')
    // Zero failures is good news; it must not be painted like bad news.
    expect(failures?.querySelector('.tile__value--bad')).toBeNull()
  })

  it('flags a failed run in the failure tiles', () => {
    const failed: LatestRun = {
      ...CLEAN,
      counts: { success: 19, error: 2, skipped: 4, pass: 36, fail: 3, warn: 0 },
    }
    render(<RunSummary run={failed} />)

    const buildFailures = screen.getByText('Build failures').parentElement
    expect(buildFailures).toHaveTextContent('2')
    expect(buildFailures?.querySelector('.tile__value--bad')).not.toBeNull()

    const testFailures = screen.getByText('Tests failed').parentElement
    expect(testFailures?.querySelector('.tile__value--bad')).not.toBeNull()
  })
})
