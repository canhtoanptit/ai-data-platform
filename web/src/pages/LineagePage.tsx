import { useQuery } from '@tanstack/react-query'
import {
  Background,
  Controls,
  type Edge,
  MarkerType,
  type Node,
  Position,
  ReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { getLineage } from '../api/client'
import type { Lineage, LineageNode } from '../api/types'
import { NODE_HEIGHT, NODE_WIDTH, layoutGraph } from '../lib/dagreLayout'
import { LAYER_ORDER, layerColor, layerTint } from '../lib/palette'
import { LayerChip } from '../components/Badges'
import { Panel } from '../components/Panel'
import { QueryState } from '../components/QueryState'

/**
 * Builds React Flow's nodes and edges from the API's DAG.
 *
 * No custom node component: a default node with a `style` and a text label is
 * enough here, and it keeps the whole rendering story in one function instead of
 * spreading it over a nodeTypes registry. The layer's own data rides along in
 * `data` so the click handler can show it.
 */
function toFlow(lineage: Lineage): { nodes: Node[]; edges: Edge[] } {
  const positioned = layoutGraph(lineage.nodes, lineage.edges)

  const nodes: Node[] = positioned.map((node) => ({
    id: node.id,
    position: node.position,
    data: { label: node.name, node },
    // Handles on the sides, not top/bottom: the layout runs left to right, and
    // React Flow's default vertical handles would send every edge on a detour.
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    style: {
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      background: layerTint(node.layer),
      border: `1px solid ${layerColor(node.layer)}`,
      borderRadius: 8,
      fontSize: 12,
      color: 'var(--ink)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    },
  }))

  const edges: Edge[] = lineage.edges.map((edge) => ({
    // React Flow needs a unique edge id; the pair is unique by construction.
    id: `${edge.source}->${edge.target}`,
    source: edge.source,
    target: edge.target,
    // Colour the edge by where it comes FROM, so a column of nodes and the
    // edges leaving it read as one layer.
    style: { stroke: 'var(--grid)' },
    markerEnd: { type: MarkerType.ArrowClosed, color: '#c3c2b7' },
  }))

  return { nodes, edges }
}

function Legend() {
  return (
    <ul className="legend-list">
      {LAYER_ORDER.map((layer) => (
        <li key={layer}>
          <LayerChip layer={layer} />
        </li>
      ))}
    </ul>
  )
}

function SelectedNode({ node }: { node: LineageNode }) {
  const isCatalogued = node.resource_type !== 'source'
  return (
    <aside className="lineage__info">
      <p className="detail__label">{node.resource_type}</p>
      <p className="lineage__info-name">{node.name}</p>
      <LayerChip layer={node.layer} />
      <p className="lineage__info-id mono">{node.id}</p>
      {isCatalogued ? (
        <Link to={`/catalog?model=${encodeURIComponent(node.name)}`}>View in catalog →</Link>
      ) : (
        // Sources are declared in a .yml, not built by dbt, so the catalog has
        // no page for them.
        <p className="detail__value--muted">Sources have no catalog entry.</p>
      )}
    </aside>
  )
}

function Graph({ lineage }: { lineage: Lineage }) {
  const [selected, setSelected] = useState<LineageNode | null>(null)
  // Laying out ~32 nodes is cheap, but it must not rerun on every click —
  // dagre would hand back new position objects and React Flow would remount
  // the whole graph, losing the pan and zoom.
  const { nodes, edges } = useMemo(() => toFlow(lineage), [lineage])

  return (
    <div className="lineage">
      <div className="lineage__canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          // fitView on load frames the whole DAG whatever its size; without it
          // the viewport starts at the origin and the graph is off-screen.
          fitView
          fitViewOptions={{ padding: 0.15 }}
          // The graph is a picture, not an editor: dragging a node or drawing an
          // edge would imply an edit that has nowhere to go. Pan and zoom stay.
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          onNodeClick={(_event, node) => setSelected(node.data.node as LineageNode)}
          onPaneClick={() => setSelected(null)}
          proOptions={{ hideAttribution: false }}
        >
          <Background color="#e1e0d9" gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      {selected && <SelectedNode node={selected} />}
    </div>
  )
}

/**
 * The dbt DAG, drawn from `parent_map` in the manifest.
 *
 * Every raw table appears twice on the left — once as the seed that loads it and
 * once as the source the staging models select from. That is not a bug in the
 * graph: this project declares both, and the `-- depends_on: ref(seed)` comment
 * in each staging model is what stops `dbt build` racing the seed. The DAG shows
 * what dbt actually holds.
 */
export function LineagePage() {
  const query = useQuery({ queryKey: ['lineage'], queryFn: getLineage })

  return (
    <Panel
      title="Lineage"
      subtitle="Sources, seeds, models and snapshots, laid out left to right"
      actions={<Legend />}
    >
      <QueryState query={query} label="the lineage graph">
        {(lineage) => <Graph lineage={lineage} />}
      </QueryState>
    </Panel>
  )
}
