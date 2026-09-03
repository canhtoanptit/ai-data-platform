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

/**
 * Airflow task/DAG-run states, mapped to the same four tones.
 *
 * A separate map from `TONES` above, because these are a *different vocabulary*
 * rather than more values in the same one — `failed` here is dbt's `error`
 * there, and `success` is the only word the two share. Merging them would build
 * one dictionary that is right about neither system's enum.
 *
 * `null` is a real state, not missing data: Airflow reports it for a task
 * instance it has created but not yet queued.
 */
const AIRFLOW_TONES: Record<string, Tone> = {
  success: 'good',
  failed: 'bad',
  // The task did not fail — its upstream did, so it never ran. Amber for the
  // same reason dbt's `skipped` is amber.
  upstream_failed: 'warn',
  skipped: 'warn',
  up_for_retry: 'warn',
  // In flight. Neutral: not yet good, and calling it bad would paint a healthy
  // run red for the seconds it takes to finish.
  running: 'neutral',
  queued: 'neutral',
}

export function taskStateTone(state: string | null): Tone {
  // `state === null` rather than a falsy check: `AIRFLOW_TONES[""]` is undefined
  // and would fall through to 'neutral' anyway, but `state && ...` widens the
  // expression's type to include the empty string.
  return (state === null ? undefined : AIRFLOW_TONES[state]) ?? 'neutral'
}

/** What a badge should say for a task Airflow has not started yet. */
export function taskStateLabel(state: string | null): string {
  return state ?? 'not started'
}
