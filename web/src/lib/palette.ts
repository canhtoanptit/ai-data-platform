/**
 * Chart colours.
 *
 * The split with `index.css` is deliberate: CSS custom properties style the DOM
 * (surfaces, ink, the health dot), and this module styles the SVG. Recharts
 * takes colours as props and writes them into SVG presentation attributes, where
 * `var()` support is not consistent across browsers — so chart marks need
 * literal hex. Both halves are steps from the same palette; keep them in step.
 *
 * The series order is the colourblind-safety mechanism, not decoration: blue ->
 * orange -> aqua was validated as a set (CVD deltaE >= 8, normal-vision >= 15,
 * all pairs, against this surface). Add a fourth series only after re-validating.
 */

const SERIES = ['#2a78d6', '#eb6834', '#1baf7a'] as const

/** Chart chrome: recessive grid and axis ink, never competing with the marks. */
export const CHART_INK = {
  grid: '#e1e0d9',
  axis: '#c3c2b7',
  label: '#898781',
  surface: '#fcfcfb',
  text: '#0b0b0b',
  textMuted: '#52514e',
} as const

/**
 * Assigns a colour per team by the team's position in a *sorted* list of teams,
 * not by its position in the API response. Colour must follow the entity: if a
 * filter drops a team, the survivors must keep the colour they already had.
 */
export function teamColors(teams: readonly string[]): Record<string, string> {
  return Object.fromEntries(
    [...teams].sort().map((team, index) => [team, SERIES[index % SERIES.length]]),
  )
}
