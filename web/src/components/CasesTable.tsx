import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getCases } from '../api/client'
import type { Case, CaseStatus } from '../api/types'
import { NO_VALUE, formatInt, formatMoney, humanize } from '../lib/format'
import { Panel } from './Panel'
import { QueryState } from './QueryState'

type StatusFilter = CaseStatus | 'all'

const STATUS_OPTIONS: ReadonlyArray<{ value: StatusFilter; label: string }> = [
  { value: 'open', label: 'Open' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'written_off', label: 'Written off' },
  { value: 'all', label: 'All statuses' },
]

function CaseRow({ row }: { row: Case }) {
  return (
    <tr>
      <td className="num">{row.case_id}</td>
      <td>{row.customer_name ?? NO_VALUE}</td>
      <td>{humanize(row.product_type)}</td>
      <td>{row.delinquency_bucket}</td>
      <td className="num">{formatInt(row.days_past_due)}</td>
      <td className="num">{formatMoney(row.delinquent_amount)}</td>
      <td className="num">{formatInt(row.contact_attempts)}</td>
    </tr>
  )
}

export function CasesTable() {
  const [status, setStatus] = useState<StatusFilter>('open')

  const query = useQuery({
    // The filter lives in the query key, which is the whole refetch mechanism:
    // change the key and react-query fetches (and caches) that filter's rows —
    // switching back is instant.
    queryKey: ['cases', status],
    queryFn: () => getCases(status === 'all' ? {} : { status }),
  })

  const filter = (
    <label className="filter">
      Status
      <select value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)}>
        {STATUS_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <Panel title="Cases" subtitle="fct_collection_cases, newest first" actions={filter}>
      <QueryState query={query} label="cases">
        {(cases) =>
          cases.length === 0 ? (
            <p className="state">No cases match this filter.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col" className="num">
                      Case
                    </th>
                    <th scope="col">Customer</th>
                    <th scope="col">Product</th>
                    <th scope="col">Bucket</th>
                    <th scope="col" className="num">
                      Days past due
                    </th>
                    <th scope="col" className="num">
                      Amount
                    </th>
                    <th scope="col" className="num">
                      Contact attempts
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((row) => (
                    <CaseRow key={row.case_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </QueryState>
    </Panel>
  )
}
