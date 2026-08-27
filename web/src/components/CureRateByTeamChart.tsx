import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { getPerformance } from '../api/client'
import type { PerformanceRow } from '../api/types'
import { formatRate, humanize } from '../lib/format'
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

interface TeamRate {
  team: string
  label: string
  cureRate: number | null
}

/**
 * Rolls the mart's per-bucket rows up to one rate per team.
 *
 * The rates are recomputed from the underlying counts rather than averaged: a
 * bucket holding 2 cases would otherwise weigh as much as one holding 200. Teams
 * with no cases get `null`, not 0 — the same "unknown" the API uses.
 */
function toTeamRates(performance: PerformanceRow[]): TeamRate[] {
  const totals = new Map<string, { cases: number; cured: number }>()
  for (const row of performance) {
    const team = row.team ?? UNASSIGNED
    const total = totals.get(team) ?? { cases: 0, cured: 0 }
    total.cases += row.case_count
    total.cured += row.cured_cases
    totals.set(team, total)
  }

  return [...totals]
    .map(([team, total]) => ({
      team,
      label: humanize(team),
      cureRate: total.cases > 0 ? (total.cured / total.cases) * 100 : null,
    }))
    // Ranked best-first: the comparison between teams is the point of the chart.
    .sort((a, b) => (b.cureRate ?? -1) - (a.cureRate ?? -1))
}

export function CureRateByTeamChart() {
  const query = useQuery({ queryKey: ['performance'], queryFn: getPerformance })

  return (
    <QueryState query={query} label="cure rate by team">
      {(performance) => {
        const rows = toTeamRates(performance)
        const colors = teamColors(rows.map((row) => row.team))
        return (
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 16, right: 8, bottom: 24, left: 16 }}>
                <CartesianGrid vertical={false} stroke={CHART_INK.grid} />
                <XAxis
                  dataKey="label"
                  tick={AXIS_TICK}
                  stroke={CHART_INK.axis}
                  label={axisLabel('Team', 'bottom')}
                />
                <YAxis
                  // Fixed 0–100: a percentage read against an auto-scaled axis
                  // makes 40% look like a full bar.
                  domain={[0, 100]}
                  tickFormatter={(value: number) => `${value}%`}
                  tick={AXIS_TICK}
                  stroke={CHART_INK.axis}
                  label={axisLabel('Cure rate', 'left')}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  itemStyle={TOOLTIP_ITEM_STYLE}
                  cursor={{ fill: 'rgba(11, 11, 11, 0.04)' }}
                  formatter={(value) => formatRate(Number(value))}
                />
                {/* One series, so no legend — the x axis names the teams. Each
                    bar keeps its team's colour from the other chart. */}
                <Bar dataKey="cureRate" name="Cure rate" radius={[4, 4, 0, 0]} maxBarSize={72}>
                  {rows.map((row) => (
                    <Cell key={row.team} fill={colors[row.team]} />
                  ))}
                  {/* Few enough bars to label directly. */}
                  <LabelList
                    dataKey="cureRate"
                    position="top"
                    formatter={(value) => formatRate(typeof value === 'number' ? value : null)}
                    style={{ fill: CHART_INK.label, fontSize: 12 }}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )
      }}
    </QueryState>
  )
}
