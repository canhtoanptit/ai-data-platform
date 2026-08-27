import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { getPerformance } from '../api/client'
import type { PerformanceRow } from '../api/types'
import { sortBuckets } from '../lib/buckets'
import { formatCompactMoney, formatMoney, humanize } from '../lib/format'
import { CHART_INK, teamColors } from '../lib/palette'
import {
  AXIS_TICK,
  TOOLTIP_ITEM_STYLE,
  TOOLTIP_LABEL_STYLE,
  TOOLTIP_STYLE,
  UNASSIGNED,
  axisLabel,
} from './chartTheme'
import { QueryState } from './QueryState'

/** One row per bucket, one numeric key per team — recharts' shape for grouped bars. */
type BucketRow = Record<string, string | number>

interface BucketSeries {
  teams: string[]
  rows: BucketRow[]
}

/** Pivots the mart's (team x bucket) rows into (bucket, team1, team2, …). */
function toBucketSeries(performance: PerformanceRow[]): BucketSeries {
  const byBucket = new Map<string, BucketRow>()
  const teams = new Set<string>()

  for (const row of performance) {
    const team = row.team ?? UNASSIGNED
    teams.add(team)
    const bucketRow = byBucket.get(row.delinquency_bucket) ?? {
      bucket: row.delinquency_bucket,
    }
    bucketRow[team] = row.delinquent_amount
    byBucket.set(row.delinquency_bucket, bucketRow)
  }

  // Buckets are ordinal, and the API returns them in GROUP BY order — sort here.
  const rows = sortBuckets(byBucket.keys()).map((bucket) => byBucket.get(bucket)!)
  return { teams: [...teams].sort(), rows }
}

export function AmountByBucketChart() {
  // Same query key as CureRateByTeamChart: react-query serves both charts from
  // one cache entry, so /api/metrics/performance is fetched once.
  const query = useQuery({ queryKey: ['performance'], queryFn: getPerformance })

  return (
    <QueryState query={query} label="performance by bucket">
      {(performance) => {
        const { teams, rows } = toBucketSeries(performance)
        const colors = teamColors(teams)
        return (
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 24, left: 16 }}>
                {/* Horizontal lines only: the value scale is the y axis. */}
                <CartesianGrid vertical={false} stroke={CHART_INK.grid} />
                <XAxis
                  dataKey="bucket"
                  tick={AXIS_TICK}
                  stroke={CHART_INK.axis}
                  label={axisLabel('Delinquency bucket', 'bottom')}
                />
                <YAxis
                  tickFormatter={formatCompactMoney}
                  tick={AXIS_TICK}
                  stroke={CHART_INK.axis}
                  label={axisLabel('Delinquent amount', 'left')}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  itemStyle={TOOLTIP_ITEM_STYLE}
                  cursor={{ fill: 'rgba(11, 11, 11, 0.04)' }}
                  formatter={(value) => formatMoney(Number(value))}
                />
                {/* Two series, so a legend is not optional. */}
                <Legend
                  formatter={(value: string) => <span className="legend__label">{value}</span>}
                />
                {teams.map((team) => (
                  <Bar
                    key={team}
                    dataKey={team}
                    name={humanize(team)}
                    fill={colors[team]}
                    radius={[4, 4, 0, 0]}
                    maxBarSize={48}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        )
      }}
    </QueryState>
  )
}
