import { useQuery } from '@tanstack/react-query'

import { getLatestRun, getLlmObservability } from '../api/client'
import type { LatestRun, RunResultRow } from '../api/types'
import { StatusBadge } from '../components/Badges'
import { LlmUsage } from '../components/LlmUsage'
import { Panel } from '../components/Panel'
import { QueryState } from '../components/QueryState'
import { RunSummary } from '../components/RunSummary'
import { formatSeconds } from '../lib/format'
import { isFailure } from '../lib/runStatus'

/**
 * Failures first, then dbt's own order.
 *
 * The API returns nodes in the order dbt ran them, which is dependency order —
 * useful for reading the run as a story, useless when 2 of 60 rows are red.
 * Sorting only by "did it fail" keeps both: the failures surface, and everything
 * else stays in build order.
 */
function failuresFirst(results: RunResultRow[]): RunResultRow[] {
  return [...results].sort(
    (a, b) => Number(isFailure(b.status)) - Number(isFailure(a.status)),
  )
}

function ResultsTable({ run }: { run: LatestRun }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Node</th>
            <th scope="col">Type</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Time
            </th>
          </tr>
        </thead>
        <tbody>
          {failuresFirst(run.results).map((row) => (
            <tr key={row.unique_id}>
              <td className="mono">
                {row.name}
                {/* Only on a failure. dbt fills `message` on success too — with
                    the adapter's response ("INSERT 8", "SELECT 3") — which is
                    noise on 60 rows and would read as an error in red. */}
                {isFailure(row.status) && row.message && (
                  <span className="run__message">{row.message}</span>
                )}
              </td>
              <td>{row.resource_type}</td>
              <td>
                <StatusBadge status={row.status} />
              </td>
              <td className="num">{formatSeconds(row.execution_time)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Pipeline observability: the last `dbt build`, node by node — plus what the AI
 * feature spent today.
 *
 * There is one run here, not a history — dbt overwrites run_results.json on
 * every invocation, so "the last run" is all the artifact holds. Ephemeral
 * models are missing for a different reason: dbt compiles them into their
 * consumers as CTEs instead of executing them, so it has nothing to report.
 *
 * The AI usage section is the opposite: `platform_ops.llm_calls` *is* a history,
 * because the API appends to it rather than overwriting a file. Two sources, one
 * page, because the question is the same one — is this thing healthy, and what
 * is it costing.
 */
export function RunsPage() {
  const query = useQuery({ queryKey: ['latest-run'], queryFn: getLatestRun })

  const usage = useQuery({
    queryKey: ['llm-observability'],
    queryFn: getLlmObservability,
    // No retry: the failure mode worth handling is "this API does not have the
    // endpoint", which retrying cannot fix, and a retried 404 delays hiding the
    // section by a few seconds of empty space.
    retry: false,
    // Traces arrive between page loads, so this one is worth refetching. Long
    // enough not to poll, short enough that a tab left open goes stale rather
    // than lying.
    staleTime: 30_000,
  })

  return (
    <>
      <Panel title="Last dbt build" subtitle="From run_results.json">
        <QueryState query={query} label="the last run">
          {(run) => <RunSummary run={run} />}
        </QueryState>
      </Panel>
      {/* Rendered only on success — no QueryState, no error banner, no loading
          shimmer. The section is a bonus on a page about dbt: an API without
          /api/observability/llm (or with the endpoint failing) should look like
          a stack that does not have the feature, not like a broken page. */}
      {usage.isSuccess && (
        <Panel
          title="AI usage"
          subtitle="Today's LLM spend and the last calls, from platform_ops.llm_calls"
        >
          <LlmUsage usage={usage.data} />
        </Panel>
      )}
      <Panel title="Nodes" subtitle="Every model, seed, snapshot and test dbt executed">
        <QueryState query={query} label="the last run">
          {(run) => <ResultsTable run={run} />}
        </QueryState>
      </Panel>
    </>
  )
}
