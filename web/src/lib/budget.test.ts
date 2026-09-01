import { describe, expect, it } from 'vitest'

import { budgetFillPct, budgetTone, formatTokenBudget } from './budget'

/**
 * The bar's geometry, which is not the same number as the label's percentage —
 * see the comment at the top of budget.ts. These tests exist mostly to pin the
 * two edge cases that would look wrong on screen: an overspent budget drawing
 * past its track, and a budget of 0 drawing as "plenty left".
 */

describe('budgetFillPct', () => {
  it('is the plain ratio in the normal case', () => {
    expect(budgetFillPct(50_000, 200_000)).toBe(25)
    expect(budgetFillPct(1_800, 200_000)).toBeCloseTo(0.9)
  })

  it('clamps an overspent budget to the width of the track', () => {
    // The last call before the limit is allowed to overshoot it — its cost is
    // not known until after it is made — so 105% is a real state, and the bar
    // must not render 105% wide.
    expect(budgetFillPct(210_000, 200_000)).toBe(100)
  })

  it('shows nothing used as empty', () => {
    expect(budgetFillPct(0, 200_000)).toBe(0)
  })

  it('draws a zero budget as full, not empty', () => {
    // A budget of 0 means no calls are allowed. An empty bar would read as
    // "plenty left", which is the opposite of the truth.
    expect(budgetFillPct(0, 0)).toBe(100)
  })
})

describe('budgetTone', () => {
  it('is ok well below the line', () => {
    expect(budgetTone(1_000, 200_000)).toBe('ok')
    expect(budgetTone(159_000, 200_000)).toBe('ok')
  })

  it('warns from 80% up', () => {
    expect(budgetTone(160_000, 200_000)).toBe('warn')
    expect(budgetTone(199_999, 200_000)).toBe('warn')
  })

  it('is spent at the limit, not past it', () => {
    // The API refuses at >=, so the bar must turn at >= too, or the UI would
    // show green while requests are being rejected.
    expect(budgetTone(200_000, 200_000)).toBe('spent')
    expect(budgetTone(240_000, 200_000)).toBe('spent')
  })
})

describe('formatTokenBudget', () => {
  it('reads as a fraction with thousands separators', () => {
    expect(formatTokenBudget(1_800, 200_000)).toBe('1,800 / 200,000 tokens')
  })
})
