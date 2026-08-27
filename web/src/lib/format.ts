/** Display formatters. Every one takes `number | null` because the API's rates
 * are nullable, and a missing rate must never be drawn as 0%. */

/** En dash: what a null (unknown) value looks like. Not "0", not "N/A". */
export const NO_VALUE = '–'

const INTEGER = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

const MONEY = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function formatInt(value: number | null): string {
  return value == null ? NO_VALUE : INTEGER.format(value)
}

/** `25` -> `25.0%`, `null` -> `–`. One decimal: the mart already rounds to one. */
export function formatRate(value: number | null): string {
  return value == null ? NO_VALUE : `${value.toFixed(1)}%`
}

/** `16630.75` -> `$16,630.75`. Exact figures: table cells and tooltips. */
export function formatMoney(value: number | null): string {
  return value == null ? NO_VALUE : MONEY.format(value)
}

const COMPACT_TIERS: ReadonlyArray<[threshold: number, suffix: string]> = [
  [1e9, 'B'],
  [1e6, 'M'],
  [1e3, 'k'],
]

/**
 * `16630.75` -> `$16.6k`. Short figures: axis ticks and KPI tiles, where the
 * exact cents cost more width than they add meaning.
 *
 * Hand-rolled rather than `Intl` `notation: 'compact'`, which renders an
 * uppercase `K` and switches to one significant digit at some magnitudes.
 */
export function formatCompactMoney(value: number | null): string {
  if (value == null) return NO_VALUE
  const magnitude = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  for (const [threshold, suffix] of COMPACT_TIERS) {
    if (magnitude >= threshold) {
      return `${sign}$${(magnitude / threshold).toFixed(1)}${suffix}`
    }
  }
  return `${sign}$${INTEGER.format(magnitude)}`
}

/** `early_stage` -> `Early stage`. The marts use snake_case for team/product. */
export function humanize(value: string | null): string {
  if (!value) return NO_VALUE
  const spaced = value.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
