import { describe, expect, it } from 'vitest'

import { bucketRank, compareBuckets, sortBuckets } from './buckets'

describe('sortBuckets', () => {
  it('puts buckets in delinquency order regardless of input order', () => {
    const scrambled = ['90+ dpd', '1-30 dpd', 'current', '61-90 dpd', '31-60 dpd']
    expect(sortBuckets(scrambled)).toEqual([
      'current',
      '1-30 dpd',
      '31-60 dpd',
      '61-90 dpd',
      '90+ dpd',
    ])
  })

  // The API returns whatever the mart's GROUP BY produced — often a subset.
  it('handles a subset', () => {
    expect(sortBuckets(['90+ dpd', '1-30 dpd'])).toEqual(['1-30 dpd', '90+ dpd'])
  })

  it('keeps unknown buckets, sorted last and alphabetically', () => {
    expect(sortBuckets(['zzz', '121+ dpd', '1-30 dpd'])).toEqual([
      '1-30 dpd',
      '121+ dpd',
      'zzz',
    ])
  })

  it('does not mutate its input', () => {
    const input = ['90+ dpd', 'current']
    sortBuckets(input)
    expect(input).toEqual(['90+ dpd', 'current'])
  })

  it('accepts any iterable, e.g. Map keys', () => {
    const seen = new Map([
      ['31-60 dpd', 1],
      ['current', 2],
    ])
    expect(sortBuckets(seen.keys())).toEqual(['current', '31-60 dpd'])
  })
})

describe('bucketRank', () => {
  it('ranks by position in the declared order', () => {
    expect(bucketRank('current')).toBe(0)
    expect(bucketRank('90+ dpd')).toBe(4)
  })

  it('ranks anything unrecognised past the end', () => {
    expect(bucketRank('made up')).toBeGreaterThan(bucketRank('90+ dpd'))
  })
})

describe('compareBuckets', () => {
  it('reports equality for the same bucket', () => {
    expect(compareBuckets('1-30 dpd', '1-30 dpd')).toBe(0)
  })

  it('would sort alphabetically the wrong way without the declared order', () => {
    // '31-60 dpd' < 'current' as strings, which is why sorting is not left to
    // localeCompare alone.
    expect('31-60 dpd'.localeCompare('current')).toBeLessThan(0)
    expect(compareBuckets('31-60 dpd', 'current')).toBeGreaterThan(0)
  })
})
