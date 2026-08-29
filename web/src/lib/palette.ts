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

/* --- dbt layer colours -------------------------------------------------------
 *
 * Identity colours for the lineage DAG, not a data series — and the distinction
 * matters. SERIES above is three colours validated as a set because a bar chart
 * can only be read by colour. A DAG node carries its name in text, its layer in
 * the legend, and its position in the left-to-right flow, so colour here is a
 * fourth, redundant cue. That is what makes seven of them acceptable when three
 * was the safe limit for chart marks.
 *
 * The order is the pipeline order and the hues walk with it — neutral inputs,
 * warm raw data, cool transforms, green outputs — so the DAG reads as a
 * progression rather than as seven unrelated categories.
 */
export const LAYER_ORDER = [
  'source',
  'seed',
  'staging',
  'intermediate',
  'marts',
  'snapshot',
  'unknown',
] as const

export type LayerName = (typeof LAYER_ORDER)[number]

const LAYER_COLORS: Record<LayerName, string> = {
  source: '#8a8781', // warm gray: not built here, just declared
  seed: '#b07d2e', // ochre: raw CSVs
  staging: '#2a78d6', // blue: the SERIES blue, cleaning
  intermediate: '#6b4fc9', // violet: business logic
  marts: '#1baf7a', // green: the gold layer
  snapshot: '#c2517a', // magenta: SCD2 history, a side branch
  unknown: '#52514e', // ink-2: a model in an unrecognised folder
}

/** Colour for a layer. Anything unrecognised gets the `unknown` neutral. */
export function layerColor(layer: string): string {
  return LAYER_COLORS[layer as LayerName] ?? LAYER_COLORS.unknown
}

/**
 * The same colour at ~12% alpha, for a node's fill. Hex-with-alpha rather than
 * a separate token per layer: one source of truth per layer, and the tint is
 * derived from it rather than kept in step by hand.
 */
export function layerTint(layer: string): string {
  return `${layerColor(layer)}1f`
}
