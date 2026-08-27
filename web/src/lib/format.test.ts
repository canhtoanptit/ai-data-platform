import { describe, expect, it } from 'vitest'

import {
  NO_VALUE,
  formatCompactMoney,
  formatInt,
  formatMoney,
  formatRate,
  humanize,
} from './format'

describe('formatRate', () => {
  it('renders one decimal place', () => {
    expect(formatRate(25)).toBe('25.0%')
    expect(formatRate(35.3)).toBe('35.3%')
  })

  // The whole reason the formatters take `number | null`: an unknown rate must
  // not be drawn as 0%.
  it('renders null as a dash, not zero', () => {
    expect(formatRate(null)).toBe(NO_VALUE)
    expect(formatRate(null)).not.toBe('0.0%')
    expect(formatRate(0)).toBe('0.0%')
  })
})

describe('formatMoney', () => {
  it('groups thousands and keeps cents', () => {
    expect(formatMoney(16630.75)).toBe('$16,630.75')
    expect(formatMoney(620.5)).toBe('$620.50')
  })

  it('renders null as a dash', () => {
    expect(formatMoney(null)).toBe(NO_VALUE)
  })
})

describe('formatCompactMoney', () => {
  it('shortens by magnitude', () => {
    expect(formatCompactMoney(16630.75)).toBe('$16.6k')
    expect(formatCompactMoney(2_400_000)).toBe('$2.4M')
    expect(formatCompactMoney(1_250_000_000)).toBe('$1.3B')
  })

  it('leaves values under a thousand alone', () => {
    expect(formatCompactMoney(980)).toBe('$980')
    expect(formatCompactMoney(0)).toBe('$0')
  })

  it('keeps the sign in front of the currency symbol', () => {
    expect(formatCompactMoney(-16630.75)).toBe('-$16.6k')
  })

  it('renders null as a dash', () => {
    expect(formatCompactMoney(null)).toBe(NO_VALUE)
  })
})

describe('formatInt', () => {
  it('groups thousands and drops decimals', () => {
    expect(formatInt(8)).toBe('8')
    expect(formatInt(12345)).toBe('12,345')
  })

  it('renders null as a dash', () => {
    expect(formatInt(null)).toBe(NO_VALUE)
  })
})

describe('humanize', () => {
  it('turns the marts snake_case into a label', () => {
    expect(humanize('early_stage')).toBe('Early stage')
    expect(humanize('credit_card')).toBe('Credit card')
  })

  it('renders null and empty strings as a dash', () => {
    expect(humanize(null)).toBe(NO_VALUE)
    expect(humanize('')).toBe(NO_VALUE)
  })
})
