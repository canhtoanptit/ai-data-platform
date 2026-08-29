import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { getCatalogModels } from '../api/client'
import type { ModelSummary } from '../api/types'
import { LayerChip } from '../components/Badges'
import { ModelDetailPanel } from '../components/ModelDetail'
import { Panel } from '../components/Panel'
import { QueryState } from '../components/QueryState'
import { LAYER_ORDER } from '../lib/palette'

const ALL_LAYERS = 'all'

/** Search matches the name or the description — both are what you half-remember. */
function matches(model: ModelSummary, search: string): boolean {
  if (search === '') return true
  const needle = search.toLowerCase()
  return (
    model.name.toLowerCase().includes(needle) ||
    model.description.toLowerCase().includes(needle)
  )
}

function ModelList({
  models,
  selected,
  onSelect,
}: {
  models: ModelSummary[]
  selected: string | null
  onSelect: (name: string) => void
}) {
  if (models.length === 0) {
    return <p className="state">Nothing matches this search.</p>
  }
  return (
    <ul className="catalog__list">
      {models.map((model) => (
        <li key={model.unique_id}>
          <button
            type="button"
            className={
              model.name === selected ? 'catalog__item catalog__item--active' : 'catalog__item'
            }
            onClick={() => onSelect(model.name)}
            // Tells a screen reader which one is showing on the right.
            aria-current={model.name === selected}
          >
            <span className="catalog__name">{model.name}</span>
            <LayerChip layer={model.layer} />
            <span className="catalog__counts">
              {model.column_count} cols · {model.test_count} tests
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

/**
 * Browse every node dbt builds, with its docs, columns and tests.
 *
 * The selected model lives in the URL (`/catalog?model=…`) rather than in
 * component state, for three things that then come free: the lineage page can
 * link straight to a model, a link into a model can be shared, and the back
 * button walks the models you looked at.
 */
export function CatalogPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [layer, setLayer] = useState<string>(ALL_LAYERS)

  const query = useQuery({ queryKey: ['catalog-models'], queryFn: getCatalogModels })
  const selected = searchParams.get('model')

  const select = (name: string) => {
    // replace: true — selecting models is browsing, not navigating, so it does
    // not deserve one history entry per click.
    setSearchParams({ model: name }, { replace: true })
  }

  const filter = (
    <label className="filter">
      Layer
      <select value={layer} onChange={(event) => setLayer(event.target.value)}>
        <option value={ALL_LAYERS}>All layers</option>
        {LAYER_ORDER.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <div className="catalog">
      <Panel title="Models" subtitle="Models, seeds and snapshots" actions={filter}>
        <input
          type="search"
          className="search"
          placeholder="Search name or description…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search models"
        />
        <QueryState query={query} label="the model catalog">
          {(models) => (
            <ModelList
              models={models.filter(
                (model) =>
                  (layer === ALL_LAYERS || model.layer === layer) && matches(model, search),
              )}
              selected={selected}
              onSelect={select}
            />
          )}
        </QueryState>
      </Panel>

      {selected === null ? (
        <Panel title="Pick a model" subtitle="Its docs, columns, tests, neighbours and SQL">
          <p className="state">Choose a model on the left to see everything dbt knows about it.</p>
        </Panel>
      ) : (
        <ModelDetailPanel
          name={selected}
          // Only names in the listing get links; sources are in the DAG but
          // have no detail page. The listing is already cached by the query
          // above, so this costs nothing.
          known={new Set((query.data ?? []).map((model) => model.name))}
        />
      )}
    </div>
  )
}
