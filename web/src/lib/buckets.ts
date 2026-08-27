/**
 * Delinquency buckets are ordinal — "31-60 dpd" comes after "1-30 dpd" — but the
 * API returns them in whatever order the mart's GROUP BY produced, and their
 * labels sort neither alphabetically ("1-30" < "31-60" < "61-90" < "90+" is a
 * coincidence that breaks the moment "current" or "121+" appears) nor
 * numerically. So the order is declared here and applied client-side.
 */

export const BUCKET_ORDER: readonly string[] = [
  'current',
  '1-30 dpd',
  '31-60 dpd',
  '61-90 dpd',
  '90+ dpd',
]

/** Position in BUCKET_ORDER; unknown labels sort last rather than disappearing. */
export function bucketRank(bucket: string): number {
  const index = BUCKET_ORDER.indexOf(bucket)
  return index === -1 ? BUCKET_ORDER.length : index
}

/** Comparator for `Array.sort`. Unrecognised labels fall to the end, A-Z. */
export function compareBuckets(a: string, b: string): number {
  const delta = bucketRank(a) - bucketRank(b)
  return delta !== 0 ? delta : a.localeCompare(b)
}

/** Sorted copy — the input is left alone (it is usually derived from query data). */
export function sortBuckets(buckets: Iterable<string>): string[] {
  return [...buckets].sort(compareBuckets)
}
