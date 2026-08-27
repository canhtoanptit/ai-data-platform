/** Recharts props shared by both charts, so the two read as one system. */

import { CHART_INK } from '../lib/palette'

/** Stand-in for a null team, so those rows are drawn rather than dropped. */
export const UNASSIGNED = 'unassigned'

export const AXIS_TICK = { fill: CHART_INK.label, fontSize: 12 } as const

export const TOOLTIP_STYLE = {
  background: CHART_INK.surface,
  border: '1px solid rgba(11, 11, 11, 0.1)',
  borderRadius: 6,
  fontSize: 13,
} as const

// Recharts colours tooltip and legend text with the series colour by default.
// Text wears text colours; the swatch beside it carries the identity — so these
// put the ink back.
export const TOOLTIP_LABEL_STYLE = { color: CHART_INK.text, fontWeight: 600 } as const
export const TOOLTIP_ITEM_STYLE = { color: CHART_INK.textMuted } as const

/** Axis titles: recharts wants a label object, and both charts want it styled alike. */
export function axisLabel(value: string, position: 'bottom' | 'left') {
  return {
    value,
    position: position === 'left' ? ('insideLeft' as const) : ('insideBottom' as const),
    angle: position === 'left' ? -90 : 0,
    offset: position === 'left' ? 0 : -16,
    style: { fill: CHART_INK.label, fontSize: 12, textAnchor: 'middle' as const },
  }
}
