import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { getCatalogModel } from '../api/client'
import type { ModelDetail as ModelDetailData } from '../api/types'
import { NO_VALUE, formatInt } from '../lib/format'
import { LayerChip } from './Badges'
import { Panel } from './Panel'
import { QueryState } from './QueryState'

interface ModelDetailProps {
  name: string
  /**
   * Names that exist in the catalog listing. A node's neighbours can include
   * things the listing does not carry — dbt sources are in the DAG but are not
   * built by dbt, so they have no detail page. Those render as plain text
   * rather than as a link to a 404.
   */
  known: ReadonlySet<string>
}

/** Upstream / downstream neighbours, as links into this same page. */
function NeighbourList({
  title,
  names,
  known,
}: {
  title: string
  names: string[]
  known: ReadonlySet<string>
}) {
  return (
    <div>
      <p className="detail__label">{title}</p>
      {names.length === 0 ? (
        <p className="detail__value detail__value--muted">Nothing</p>
      ) : (
        <ul className="link-list">
          {names.map((name) => (
            <li key={name}>
              {known.has(name) ? (
                <Link to={`/catalog?model=${encodeURIComponent(name)}`}>{name}</Link>
              ) : (
                <span className="detail__value--muted">{name}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SqlBlock({ title, sql }: { title: string; sql: string | null }) {
  if (!sql) return null
  return (
    // <details> rather than a state-managed accordion: SQL is long, most visits
    // do not want it, and the browser already has this widget.
    <details className="sql">
      <summary>{title}</summary>
      <pre className="code">{sql}</pre>
    </details>
  )
}

function ColumnsTable({ columns }: { columns: ModelDetailData['columns'] }) {
  if (columns.length === 0) {
    return (
      <p className="state">
        No columns. Ephemeral models are never built, so there is nothing in the warehouse
        to describe.
      </p>
    )
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Type</th>
            <th scope="col">Description</th>
            <th scope="col">Tests</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((column) => (
            <tr key={column.name}>
              <td className="mono">{column.name}</td>
              <td className="mono detail__value--muted">{column.data_type ?? NO_VALUE}</td>
              <td className="wrap">{column.description ?? NO_VALUE}</td>
              <td>
                {column.tests.length === 0
                  ? NO_VALUE
                  : column.tests.map((test) => (
                      <span key={test} className="chip chip--test">
                        {test}
                      </span>
                    ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Detail({ model, known }: { model: ModelDetailData; known: ReadonlySet<string> }) {
  return (
    <>
      <div className="detail__meta">
        <LayerChip layer={model.layer} />
        <span className="chip">{model.materialization}</span>
        <span className="chip">{model.schema}</span>
      </div>

      <p className="detail__description">
        {model.description || 'No description — add one to the model’s .yml file.'}
      </p>

      <div className="detail__grid">
        <div>
          <p className="detail__label">Columns</p>
          <p className="detail__value">{formatInt(model.column_count)}</p>
        </div>
        <div>
          <p className="detail__label">Tests</p>
          <p className="detail__value">{formatInt(model.test_count)}</p>
        </div>
        <NeighbourList title="Depends on" names={model.depends_on} known={known} />
        <NeighbourList title="Referenced by" names={model.referenced_by} known={known} />
      </div>

      {model.table_tests.length > 0 && (
        <p className="detail__tabletests">
          <span className="detail__label">Table-level tests</span>{' '}
          {model.table_tests.map((test) => (
            <span key={test} className="chip chip--test">
              {test}
            </span>
          ))}
        </p>
      )}

      <ColumnsTable columns={model.columns} />

      <SqlBlock title="Raw SQL (as written, Jinja included)" sql={model.raw_sql} />
      <SqlBlock title="Compiled SQL (what the warehouse ran)" sql={model.compiled_sql} />
    </>
  )
}

/** The right-hand panel of the catalog: everything dbt knows about one node. */
export function ModelDetailPanel({ name, known }: ModelDetailProps) {
  const query = useQuery({
    queryKey: ['catalog-model', name],
    queryFn: () => getCatalogModel(name),
  })

  return (
    <Panel title={name} subtitle="From dbt's manifest and catalog">
      <QueryState query={query} label={`the ${name} model`}>
        {(model) => <Detail model={model} known={known} />}
      </QueryState>
    </Panel>
  )
}
