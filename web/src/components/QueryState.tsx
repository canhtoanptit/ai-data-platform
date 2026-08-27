import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'

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
    return (
      <p className="state state--error" role="alert">
        <strong>Could not load {label}.</strong> {query.error.message}
      </p>
    )
  }
  return <>{children(query.data)}</>
}
