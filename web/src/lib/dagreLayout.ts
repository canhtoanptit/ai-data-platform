/**
 * Turns a dbt DAG into positioned nodes, left to right.
 *
 * React Flow renders nodes at coordinates you give it; it has no opinion about
 * where they should go. dagre is the layered-graph layout algorithm that has
 * the opinion — it assigns each node a "rank" (its depth in the DAG) and orders
 * nodes within a rank to minimise edge crossings. With `rankdir: 'LR'` that
 * produces exactly the picture a data pipeline wants: sources on the left,
 * marts on the right, one column per hop.
 *
 * Kept as a pure function — graph in, positions out, no React and no xyflow
 * imports — so the layout can be unit-tested without a DOM, and so the page
 * component is left doing nothing but rendering.
 */

import dagre from 'dagre'

/** Node box, in pixels. Must match the CSS that draws the node. */
export const NODE_WIDTH = 190
export const NODE_HEIGHT = 44

export interface XY {
  x: number
  y: number
}

export interface GraphEdge {
  source: string
  target: string
}

export interface LayoutOptions {
  /** Gap between ranks (columns, in LR). */
  rankSeparation?: number
  /** Gap between nodes inside a rank. */
  nodeSeparation?: number
}

/**
 * Positions every node and returns copies with a `position` added — the shape
 * React Flow wants. The input is left untouched.
 *
 * Generic over the node type so the caller keeps its own fields (name, layer,
 * …) instead of having to re-join them afterwards.
 */
export function layoutGraph<N extends { id: string }>(
  nodes: readonly N[],
  edges: readonly GraphEdge[],
  { rankSeparation = 120, nodeSeparation = 24 }: LayoutOptions = {},
): Array<N & { position: XY }> {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', ranksep: rankSeparation, nodesep: nodeSeparation })
  // dagre requires a default edge-label factory even when no edge has a label.
  graph.setDefaultEdgeLabel(() => ({}))

  const known = new Set(nodes.map((node) => node.id))
  for (const node of nodes) {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const edge of edges) {
    // Skip edges pointing at nodes we were not given: dagre would silently
    // invent the missing endpoint as a real node, and an unlabelled ghost box
    // would appear in the diagram.
    if (known.has(edge.source) && known.has(edge.target)) {
      graph.setEdge(edge.source, edge.target)
    }
  }

  dagre.layout(graph)

  return nodes.map((node) => {
    const positioned = graph.node(node.id)
    return {
      ...node,
      // dagre returns the node's CENTRE; React Flow positions by TOP-LEFT.
      // Without this shift every node sits half a box down and to the right of
      // where the edges are drawn to.
      position: {
        x: positioned.x - NODE_WIDTH / 2,
        y: positioned.y - NODE_HEIGHT / 2,
      },
    }
  })
}
