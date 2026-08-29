import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { ApiError } from '../api/client'

interface QueryStateProps<T> {
  query: UseQueryResult<T, Error>
  /** Named in the loading/error copy, e.g. "the KPI summary". */
  label: string
  children: (data: T) => ReactNode
}

/**
 * The one place loading and error states are spelled out, so every section
 * behaves identically. The render-prop child is what makes it typed: react-query
 * narrows `data` to `T` only after `isPending`/`isError` are ruled out, which is
 * exactly where the child is called.
 */
export function QueryState<T>({ query, label, children }: QueryStateProps<T>) {
  if (query.isPending) {
    return <p className="state">Loading {label}…</p>
  }
  if (query.isError) {
    // 503 is not a failure to report — it is "dbt hasn't run yet", which the
    // catalog, lineage and runs pages all hit before the first build. The API
    // sends the fix as its `detail`, so show that instead of an error.
    if (query.error instanceof ApiError && query.error.status === 503) {
      return (
        <div className="empty">
          <p className="empty__title">No dbt artifacts yet</p>
          <p className="empty__body">
            This page is built from the files dbt writes to <code>anz_banking/target/</code>.
            Generate them from the repo root:
          </p>
          <pre className="code code--inline">make local-build && make local-docs</pre>
          <p className="empty__detail">{query.error.message}</p>
        </div>
      )
    }
    return (
      <p className="state state--error" role="alert">
        <strong>Could not load {label}.</strong> {query.error.message}
      </p>
    )
  }
  return <>{children(query.data)}</>
}
