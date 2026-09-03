import { Link } from 'react-router-dom'

import type { IngestRunStatus } from '../api/types'
import { formatSeconds } from '../lib/format'
import { taskStateLabel, taskStateTone } from '../lib/runStatus'

/**
 * What each DAG task actually does, in words. The task_id is the contract with
 * Airflow and stays visible in mono, but "copy_into_raw" is a name, not an
 * explanation — and this panel is the one place a reader learns what the
 * pipeline is made of.
 */
const TASK_DESCRIPTIONS: Record<string, string> = {
  copy_into_raw: 'Bulk COPY of the file into the raw landing table',
  dbt_build_downstream: 'dbt build of the touched staging model and everything downstream — tests included',
}

/** Airflow's four dag-run states, as a headline and a colour. */
function banner(run: IngestRunStatus): { tone: string; title: string; body: string } {
  if (run.state === 'success') {
    return {
      tone: 'good',
      title: 'Loaded, transformed and tested',
      body:
        'The rows are in the raw table, the downstream models are rebuilt, and '
        + 'every test on them passed.',
    }
  }
  if (run.state === 'failed') {
    return {
      tone: 'bad',
      title: 'The pipeline stopped',
      body:
        'One of the tasks below failed. If it was the dbt task, a data test '
        + 'refused the new rows — which is the quality gate working: nothing '
        + 'downstream was published from data that failed its contract.',
    }
  }
  return {
    tone: 'neutral',
    title: run.state === 'queued' ? 'Queued' : 'Running',
    body: 'Airflow is working through the two tasks. This panel refreshes itself.',
  }
}

/**
 * Live progress for one ingestion run: two tasks, a verdict, and where to look
 * next.
 *
 * The parent owns the polling (it knows when to stop — the API's `is_running`
 * says so); this component only renders a snapshot, which is what makes it
 * testable against a fixed state.
 */
export function IngestRunPanel({ run }: { run: IngestRunStatus }) {
  const { tone, title, body } = banner(run)

  return (
    <div className="ingest__run">
      <div
        className={`ingest__banner ingest__banner--${tone}`}
        // Announced, because the user is waiting on this and may well have
        // looked away. Not `alert`: a failure here is an expected outcome of the
        // demo, not a broken page.
        role="status"
      >
        <p className="ingest__banner-title">{title}</p>
        <p className="ingest__banner-body">{body}</p>
      </div>

      <ol className="ingest__tasks">
        {run.tasks.map((task) => (
          <li key={task.task_id} className="ingest__task">
            <div className="ingest__task-head">
              <span className="mono">{task.task_id}</span>
              <span className={`badge badge--${taskStateTone(task.state)}`}>
                {taskStateLabel(task.state)}
              </span>
            </div>
            <p className="ingest__task-body">{TASK_DESCRIPTIONS[task.task_id] ?? ''}</p>
            {/* Only once it has run. A duration of "–" on a queued task reads as
                missing data rather than as "not yet". */}
            {task.duration_seconds != null && (
              <p className="ingest__task-meta">took {formatSeconds(task.duration_seconds)}</p>
            )}
          </li>
        ))}
        {run.tasks.length === 0 && (
          // Between the trigger and the scheduler's first look there is a real
          // moment with a run and no task instances yet.
          <li className="ingest__task">
            <p className="ingest__task-body">Waiting for the scheduler to pick up the run…</p>
          </li>
        )}
      </ol>

      <p className="ingest__run-id">
        Airflow run <span className="mono">{run.dag_run_id}</span>
      </p>

      {/* Only on a settled run: mid-flight, "see the numbers move" points at
          numbers that have not moved yet. */}
      {!run.is_running && (
        <p className="ingest__links">
          <Link to="/runs">See the dbt run it triggered →</Link>
          <Link to="/">Back to the dashboard →</Link>
        </p>
      )}
    </div>
  )
}
