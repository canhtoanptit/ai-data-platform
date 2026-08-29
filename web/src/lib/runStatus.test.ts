import { describe, expect, it } from 'vitest'

import { isFailure, statusTone } from './runStatus'

describe('statusTone', () => {
  it('maps model statuses', () => {
    expect(statusTone('success')).toBe('good')
    expect(statusTone('error')).toBe('bad')
  })

  it('maps test statuses', () => {
    expect(statusTone('pass')).toBe('good')
    expect(statusTone('fail')).toBe('bad')
    expect(statusTone('warn')).toBe('warn')
  })

  // A skipped node did not fail — its upstream did. Amber, not red.
  it('treats skipped as a warning, not a failure', () => {
    expect(statusTone('skipped')).toBe('warn')
    expect(isFailure('skipped')).toBe(false)
  })

  // dbt has added statuses over releases (no-op, reused, partial success). An
  // unknown one must never come out green.
  it('is neutral about statuses it has never seen', () => {
    expect(statusTone('no-op')).toBe('neutral')
    expect(statusTone('')).toBe('neutral')
  })
})

describe('isFailure', () => {
  it('is true only for the red statuses', () => {
    expect(isFailure('error')).toBe(true)
    expect(isFailure('fail')).toBe(true)
    expect(isFailure('success')).toBe(false)
    expect(isFailure('pass')).toBe(false)
    expect(isFailure('warn')).toBe(false)
  })
})
