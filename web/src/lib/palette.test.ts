import { describe, expect, it } from 'vitest'

import { LAYER_ORDER, layerColor, layerTint, teamColors } from './palette'

describe('layerColor', () => {
  it('gives every layer its own colour', () => {
    const colors = LAYER_ORDER.map(layerColor)
    expect(new Set(colors).size).toBe(LAYER_ORDER.length)
  })

  it('returns a hex colour', () => {
    for (const layer of LAYER_ORDER) {
      expect(layerColor(layer)).toMatch(/^#[0-9a-f]{6}$/)
    }
  })

  // The API's `layer` is a string, and dbt could grow a folder this UI has not
  // heard of. A missing colour must be a neutral, not `undefined` in the DOM.
  it('falls back to the unknown neutral for an unrecognised layer', () => {
    expect(layerColor('lakehouse')).toBe(layerColor('unknown'))
  })
})

describe('layerTint', () => {
  it('is the layer’s own colour with an alpha channel', () => {
    expect(layerTint('marts')).toBe(`${layerColor('marts')}1f`)
  })
})

describe('teamColors', () => {
  // Guards the reason the chart palette is separate: colour follows the entity,
  // not its index in the response.
  it('assigns by sorted team name, not by input order', () => {
    const forward = teamColors(['early_stage', 'late_stage'])
    const reversed = teamColors(['late_stage', 'early_stage'])
    expect(forward).toEqual(reversed)
  })
})
