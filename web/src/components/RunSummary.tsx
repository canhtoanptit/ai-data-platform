import type { LatestRun } from '../api/types'
import { formatInt, formatSeconds, formatTimestamp } from '../lib/format'

interface Tile {
  label: string
  value: string
  /** Turns the number red when it is a count of things that went wrong. */
  tone?: 'bad'
}

/**
 * Five tiles: what got built, what got tested, and when.
 *
 * Failures get their own tiles rather than being folded into "12 of 12 models"
 * — a zero there is the answer you want to be able to find in one glance, and a
 * non-zero one is the reason you opened the page.
 */
function tilesFor(run: LatestRun): Tile[] {
  const { counts } = run
  return [
    { label: 'Models built', value: formatInt(counts.success) },
    {
      label: 'Build failures',
      value: formatInt(counts.error),
      tone: counts.error > 0 ? 'bad' : undefined,
    },
    { label: 'Tests passed', value: formatInt(counts.pass) },
    {
      label: 'Tests failed',
      value: formatInt(counts.fail + counts.warn),
      tone: counts.fail > 0 ? 'bad' : undefined,
    },
    { label: 'Total elapsed', value: formatSeconds(run.elapsed_total) },
    { label: 'Run at', value: formatTimestamp(run.generated_at) },
  ]
}

/** Presentational on purpose: no fetching here, so it is trivial to test. */
export function RunSummary({ run }: { run: LatestRun }) {
  return (
    <ul className="tiles">
      {tilesFor(run).map((tile) => (
        <li key={tile.label} className="tile">
          <p className="tile__label">{tile.label}</p>
          <p className={tile.tone === 'bad' ? 'tile__value tile__value--bad' : 'tile__value'}>
            {tile.value}
          </p>
        </li>
      ))}
    </ul>
  )
}
