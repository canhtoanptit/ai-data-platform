/**
 * dbt run statuses, mapped to something a badge can wear.
 *
 * dbt does not have one status enum. Models, seeds and snapshots report
 * `success | error | skipped`; tests report `pass | fail | warn` (and can be
 * `skipped` when an upstream model failed). The observability table mixes both
 * kinds of row, so it needs one mapping that covers all of them.
 *
 * The tone is a *semantic* name, not a colour — index.css owns the colours, so
 * the palette stays in one file and this stays testable without a DOM.
 */

export type Tone = 'good' | 'bad' | 'warn' | 'neutral'

const TONES: Record<string, Tone> = {
  // Models, seeds, snapshots.
  success: 'good',
  error: 'bad',
  // Tests.
  pass: 'good',
  fail: 'bad',
  warn: 'warn',
  // Either kind: dbt skips a node whose upstream failed. Amber, not red — it
  // did not fail, it never got the chance to run.
  skipped: 'warn',
}

/**
 * Anything unrecognised is `neutral`, not `good`: dbt has added statuses over
 * time (`no-op`, `reused`, `partial success`), and a new one must never be
 * painted green by default.
 */
export function statusTone(status: string): Tone {
  return TONES[status] ?? 'neutral'
}

/** True for the statuses worth sorting to the top of the table. */
export function isFailure(status: string): boolean {
  return statusTone(status) === 'bad'
}
