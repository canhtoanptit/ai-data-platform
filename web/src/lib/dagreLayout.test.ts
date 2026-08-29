import { describe, expect, it } from 'vitest'

import { NODE_HEIGHT, NODE_WIDTH, layoutGraph } from './dagreLayout'

/** A miniature of the real DAG: seed -> staging -> fact, plus a side branch. */
const NODES = [
  { id: 'seed.raw_cases', layer: 'seed' },
  { id: 'model.stg_cases', layer: 'staging' },
  { id: 'model.int_ptp', layer: 'intermediate' },
  { id: 'model.fct_cases', layer: 'marts' },
]

const EDGES = [
  { source: 'seed.raw_cases', target: 'model.stg_cases' },
  { source: 'model.stg_cases', target: 'model.fct_cases' },
  { source: 'model.int_ptp', target: 'model.fct_cases' },
]

function positions(nodes: ReturnType<typeof layoutGraph>) {
  return Object.fromEntries(nodes.map((node) => [node.id, node.position]))
}

describe('layoutGraph', () => {
  it('positions every node it is given', () => {
    const laid = layoutGraph(NODES, EDGES)
    expect(laid).toHaveLength(NODES.length)
    for (const node of laid) {
      expect(Number.isFinite(node.position.x)).toBe(true)
      expect(Number.isFinite(node.position.y)).toBe(true)
    }
  })

  it('keeps the caller’s own fields', () => {
    const laid = layoutGraph(NODES, EDGES)
    expect(laid.find((node) => node.id === 'model.fct_cases')?.layer).toBe('marts')
  })

  // The whole point of rankdir: 'LR' — data flows rightwards, so downstream
  // must sit to the right of upstream.
  it('lays the DAG out left to right', () => {
    const at = positions(layoutGraph(NODES, EDGES))
    expect(at['model.stg_cases'].x).toBeGreaterThan(at['seed.raw_cases'].x)
    expect(at['model.fct_cases'].x).toBeGreaterThan(at['model.stg_cases'].x)
  })

  it('puts nodes of the same rank in different places', () => {
    // stg_cases and int_ptp both feed the fact, so they share a column and must
    // not be drawn on top of each other.
    const at = positions(layoutGraph(NODES, EDGES))
    expect(at['model.stg_cases'].y).not.toBe(at['model.int_ptp'].y)
  })

  it('lays out a node with no edges at all', () => {
    // An unreferenced seed is a real case (raw_accounts_cdc had no consumer
    // until int_accounts_cdc existed) and must not disappear.
    const laid = layoutGraph([...NODES, { id: 'seed.orphan', layer: 'seed' }], EDGES)
    expect(laid.map((node) => node.id)).toContain('seed.orphan')
  })

  // dagre would otherwise invent the missing endpoint as an unlabelled node.
  it('ignores edges pointing at nodes it was not given', () => {
    const laid = layoutGraph(NODES, [
      ...EDGES,
      { source: 'model.fct_cases', target: 'model.does_not_exist' },
    ])
    expect(laid).toHaveLength(NODES.length)
  })

  it('shifts dagre’s centre coordinates to React Flow’s top-left', () => {
    // A single node is centred on its own box, so its top-left is exactly half
    // a box up and to the left of that centre.
    const [only] = layoutGraph([{ id: 'solo' }], [])
    expect(only.position.x).toBe(NODE_WIDTH / 2 - NODE_WIDTH / 2)
    expect(only.position.y).toBe(NODE_HEIGHT / 2 - NODE_HEIGHT / 2)
  })

  it('does not mutate its input', () => {
    const input = [{ id: 'a' }]
    layoutGraph(input, [])
    expect(input[0]).not.toHaveProperty('position')
  })
})
