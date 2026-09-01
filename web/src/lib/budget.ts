/**
 * Budget-bar arithmetic, kept out of the component so it can be tested without
 * a DOM.
 *
 * The API already sends `budget_used_pct`, and this recomputes it. That is
 * deliberate: the *bar width* must be clamped to 0–100 or a spent budget draws
 * outside its track, and the server's figure is an honest percentage that can
 * exceed 100 (the last call before the limit is allowed to overshoot it — its
 * cost is not known until after it is made). Two different numbers for two
 * different jobs: the server's for the label, this one for the geometry.
 */

/** How full the bar is drawn, as a percentage clamped to the track. */
export function budgetFillPct(tokens: number, budget: number): number {
  // A budget of 0 means "no calls allowed". Drawing that as an empty bar would
  // read as "plenty left", so it is full.
  if (budget <= 0) return 100
  if (tokens <= 0) return 0
  return Math.min(100, (tokens / budget) * 100)
}

export type BudgetTone = 'ok' | 'warn' | 'spent'

/**
 * Three states, because they call for three different reactions: fine, getting
 * close, and requests are being refused right now.
 *
 * 80% is the warning line — early enough to be a heads-up, late enough not to
 * be permanently amber.
 */
export function budgetTone(tokens: number, budget: number): BudgetTone {
  if (budget <= 0 || tokens >= budget) return 'spent'
  if (tokens / budget >= 0.8) return 'warn'
  return 'ok'
}

/** `1800` of `200000` -> `1,800 / 200,000 tokens`. */
export function formatTokenBudget(tokens: number, budget: number): string {
  const format = (value: number) => value.toLocaleString('en-US')
  return `${format(tokens)} / ${format(budget)} tokens`
}
