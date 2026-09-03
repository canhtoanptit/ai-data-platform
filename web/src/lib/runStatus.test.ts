import { describe, expect, it } from 'vitest'

import { isFailure, statusTone, taskStateLabel, taskStateTone } from './runStatus'

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

describe('taskStateTone', () => {
  // Airflow's vocabulary, not dbt's: `failed`, not `error`.
  it('maps Airflow terminal states', () => {
    expect(taskStateTone('success')).toBe('good')
    expect(taskStateTone('failed')).toBe('bad')
  })

  // The task itself did not fail; it never got to run. Same reasoning as dbt's
  // skipped.
  it('treats upstream_failed as a warning', () => {
    expect(taskStateTone('upstream_failed')).toBe('warn')
    expect(taskStateTone('skipped')).toBe('warn')
  })

  // A run in flight must not be painted red for the seconds it takes.
  it('is neutral while a task is in flight', () => {
    expect(taskStateTone('running')).toBe('neutral')
    expect(taskStateTone('queued')).toBe('neutral')
  })

  // Airflow reports null for a task instance it has created but not queued.
  it('is neutral for a task that has not started', () => {
    expect(taskStateTone(null)).toBe('neutral')
    expect(taskStateLabel(null)).toBe('not started')
    expect(taskStateLabel('running')).toBe('running')
  })

  // dbt's `error` is not an Airflow state; it must not be quietly mapped.
  it('is neutral about states it has never seen', () => {
    expect(taskStateTone('error')).toBe('neutral')
    expect(taskStateTone('deferred')).toBe('neutral')
  })
})
