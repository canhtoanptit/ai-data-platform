import { useQuery } from '@tanstack/react-query'

import { getSummary } from '../api/client'
import type { MetricsSummary } from '../api/types'
import { formatCompactMoney, formatInt, formatRate } from '../lib/format'
import { QueryState } from './QueryState'

interface Tile {
  label: string
  value: string
}

function tilesFor(summary: MetricsSummary): Tile[] {
  return [
    { label: 'Total cases', value: formatInt(summary.total_cases) },
    { label: 'Open cases', value: formatInt(summary.open_cases) },
    {
      label: 'Delinquent amount',
      value: formatCompactMoney(summary.total_delinquent_amount),
    },
    { label: 'Cure rate', value: formatRate(summary.cure_rate_pct) },
    { label: 'PTP kept rate', value: formatRate(summary.ptp_kept_rate_pct) },
    { label: 'Right-party contact rate', value: formatRate(summary.rpc_rate_pct) },
  ]
}

/** Presentational on purpose: no fetching here, so it is trivial to test. */
export function KpiTiles({ summary }: { summary: MetricsSummary }) {
  return (
    <ul className="tiles">
      {tilesFor(summary).map((tile) => (
        <li key={tile.label} className="tile">
          <p className="tile__label">{tile.label}</p>
          <p className="tile__value">{tile.value}</p>
        </li>
      ))}
    </ul>
  )
}

export function KpiTilesSection() {
  const query = useQuery({ queryKey: ['summary'], queryFn: getSummary })
  return (
    <QueryState query={query} label="the portfolio summary">
      {(summary) => <KpiTiles summary={summary} />}
    </QueryState>
  )
}
