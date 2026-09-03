import { useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import {
  getIngestRun,
  getIngestTables,
  isHeaderMismatch,
  isPipelineOffline,
  uploadIngestFile,
} from '../api/client'
import type { IngestAccepted } from '../api/types'
import { IngestRunPanel } from '../components/IngestRunPanel'
import { Panel } from '../components/Panel'
import { QueryState } from '../components/QueryState'

/** Downloadable files in web/public/samples/, one per format the API accepts. */
const SAMPLES = [
  { href: '/samples/new_payments.csv', label: 'new_payments.csv', note: 'comma-separated' },
  { href: '/samples/new_payments.psv', label: 'new_payments.psv', note: 'pipe-delimited' },
] as const

/** How often to re-read a run that is still going. */
const POLL_MS = 1200

/**
 * Drop a file, watch a pipeline run.
 *
 * This is the write end of the platform, and the only page that starts anything.
 * What happens after the drop is deliberately not hidden:
 *
 *   1. the API validates the file (table, extension, size, header) and stages it
 *      in a volume it shares with Airflow;
 *   2. Airflow's `file_ingest` DAG bulk-COPYs the rows into the raw landing
 *      table — append, never replace;
 *   3. the same DAG runs `dbt build` scoped to the touched staging model and
 *      everything downstream of it, tests included;
 *   4. dbt's artifacts land in the directory the API already reads, so the Runs
 *      page shows this run with no extra plumbing.
 *
 * Step 3 is where the interesting failure lives, and the page says so up front:
 * upload the same file twice and the `unique` test on the primary key fails the
 * build. That is the point of a test in a pipeline rather than a check in a
 * dashboard — the bad rows are in the landing table (as they always can be) and
 * never reach a mart.
 *
 * Local state plus a plain async call for the upload, like ChatPage: uploading is
 * an action with a one-off result, not shared server state worth a cache key. The
 * *run* that follows is server state, so that half is a TanStack query with a
 * polling interval.
 */
export function IngestPage() {
  const tables = useQuery({ queryKey: ['ingest-tables'], queryFn: getIngestTables })

  const [tableName, setTableName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [accepted, setAccepted] = useState<IngestAccepted | null>(null)
  const [uploadError, setUploadError] = useState<unknown>(null)
  // Reset the <input type="file"> after a successful upload, so re-selecting the
  // *same* file fires a change event — which is exactly the duplicate-upload
  // demo. Without this the browser considers it unchanged and stays silent.
  const fileInput = useRef<HTMLInputElement>(null)

  // Falls back to the first table rather than to a hardcoded name: the allow-list
  // lives in the API, and the page should not have a second opinion about it.
  const selected = tables.data?.find((row) => row.name === tableName) ?? tables.data?.[0]

  const runId = accepted?.dag_run_id ?? null
  const run = useQuery({
    queryKey: ['ingest-run', runId],
    // Non-null by the time this runs — `enabled` gates it on exactly that.
    queryFn: () => getIngestRun(runId as string),
    enabled: runId !== null,
    // No retry: this polls anyway, so a retry only adds requests to the same
    // second. A genuine failure (Airflow stopped mid-run) should show, not hide.
    retry: false,
    // Poll while the API says the run is unfinished, then stop. The server
    // computes `is_running` so the client never has to know Airflow's state
    // vocabulary — and two clients cannot disagree about when a run is over.
    refetchInterval: (query) =>
      query.state.data && !query.state.data.is_running ? false : POLL_MS,
  })

  async function submit() {
    if (!file || !selected || uploading) return
    setUploading(true)
    setUploadError(null)
    // Clear the previous run before starting a new one, so the panel never shows
    // the last upload's verdict next to this upload's file.
    setAccepted(null)
    try {
      setAccepted(await uploadIngestFile({ table: selected.name, file }))
      setFile(null)
      if (fileInput.current) fileInput.current.value = ''
    } catch (error) {
      setUploadError(error)
    } finally {
      setUploading(false)
    }
  }

  return (
    <>
      <Panel
        title="Ingest a file"
        subtitle="Upload a CSV or pipe-delimited extract; Airflow loads it and dbt rebuilds what depends on it"
      >
        <QueryState query={tables} label="the ingestable tables">
          {(rows) => (
            <div className="ingest">
              <label className="ingest__label" htmlFor="ingest-table">
                Destination table
              </label>
              <select
                id="ingest-table"
                className="ingest__select"
                value={selected?.name ?? ''}
                onChange={(event) => setTableName(event.target.value)}
              >
                {rows.map((row) => (
                  <option key={row.name} value={row.name}>
                    {row.label} — {row.name}
                  </option>
                ))}
              </select>

              {selected && (
                <p className="ingest__columns">
                  Expected header (any order):{' '}
                  <span className="mono">{selected.columns.join(', ')}</span>
                  <br />
                  Builds <span className="mono">{selected.staging_model}</span> and everything
                  downstream.
                </p>
              )}

              <div
                className={dragging ? 'ingest__drop ingest__drop--active' : 'ingest__drop'}
                // preventDefault on dragover is what makes an element a drop
                // target at all — without it the browser navigates to the file.
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault()
                  setDragging(false)
                  const dropped = event.dataTransfer.files[0]
                  if (dropped) setFile(dropped)
                }}
              >
                <p className="ingest__drop-hint">Drag a file here, or</p>
                <input
                  ref={fileInput}
                  id="ingest-file"
                  className="ingest__file"
                  type="file"
                  // A hint to the file picker, not a guarantee: the API checks the
                  // extension itself, because `accept` is trivially bypassed.
                  accept=".csv,.psv,.txt"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
                <label className="ingest__browse" htmlFor="ingest-file">
                  choose a file
                </label>
                {file && (
                  <p className="ingest__chosen">
                    <span className="mono">{file.name}</span> ({Math.max(1, Math.round(file.size / 1024))} KB)
                  </p>
                )}
              </div>

              <div className="ingest__actions">
                <button
                  type="button"
                  className="chat__send"
                  onClick={() => void submit()}
                  disabled={uploading || file === null}
                >
                  {uploading ? 'Uploading…' : 'Upload and run the pipeline'}
                </button>
                <span className="ingest__samples">
                  No file handy? Try{' '}
                  {SAMPLES.map((sample, index) => (
                    <span key={sample.href}>
                      {index > 0 && ' or '}
                      {/* `download` so a click saves the file instead of
                          rendering the CSV as text in the tab. */}
                      <a href={sample.href} download>
                        {sample.label}
                      </a>{' '}
                      ({sample.note})
                    </span>
                  ))}
                  . Both add 5 payments, ids 9016–9020.
                </span>
              </div>

              {uploadError !== null && <UploadFailure error={uploadError} />}
            </div>
          )}
        </QueryState>
      </Panel>

      {runId !== null && (
        <Panel
          title="Pipeline run"
          subtitle={`Airflow DAG file_ingest · polling every ${POLL_MS / 1000}s`}
        >
          {/* Not QueryState, unlike every other section: its 503 branch says
              "no dbt artifacts yet — run make local-build", which is the right
              message for the catalog pages and the wrong one here. A 503 on this
              query means Airflow stopped, so it reuses the same renderer the
              upload does. */}
          {run.isPending && <p className="state">Starting the run…</p>}
          {run.isError && <UploadFailure error={run.error} />}
          {run.isSuccess && <IngestRunPanel run={run.data} />}
        </Panel>
      )}

      <Panel title="What happens when you upload" subtitle="And why the second upload of the same file fails">
        <div className="ingest__explainer">
          <ol>
            <li>
              <strong>Validate.</strong> The API checks the table is on its allow-list, the
              extension is one it parses, the file is under 5 MB, and the header matches the
              table&apos;s columns — a fast, specific rejection beats a red DAG twenty seconds
              later.
            </li>
            <li>
              <strong>Stage and hand off.</strong> The file is written into a volume shared with
              Airflow, and only its <em>path</em> is sent in the DAG run&apos;s config. A 5 MB CSV
              never travels through Airflow&apos;s metadata database.
            </li>
            <li>
              <strong>COPY, appending.</strong> Airflow bulk-loads the rows into the raw landing
              table — Postgres&apos; equivalent of Snowflake&apos;s <span className="mono">COPY INTO</span>{' '}
              from a stage. Raw tables accumulate what arrived; nothing is replaced.
            </li>
            <li>
              <strong>dbt build, scoped.</strong> Only the touched staging model and its
              descendants are rebuilt, with their tests. That keeps the run seconds long and makes{' '}
              <span className="mono">run_results.json</span> a record of exactly this load — which
              is what the <a href="/runs">Runs</a> page then shows.
            </li>
          </ol>
          <p className="ingest__explainer-note">
            <strong>Upload the same file twice on purpose.</strong> The second load duplicates the
            primary keys, dbt&apos;s <span className="mono">unique</span> test fails, the build exits
            non-zero and the task goes red. That is the feature: the bad rows sit in the raw landing
            table where they can be inspected and removed, and nothing downstream was published from
            data that failed its contract. A dashboard that quietly showed doubled totals would be
            the alternative.
          </p>
          <p className="ingest__explainer-note">
            Payments feed <span className="mono">dim_customers.total_paid</span>, so that is the
            mart figure that moves for the sample files. The dashboard&apos;s case KPIs are driven by{' '}
            <span className="mono">raw_collection_cases</span> — upload to that table to move them.
          </p>
        </div>
      </Panel>
    </>
  )
}

/**
 * Three failures worth telling apart, because the reader's next action differs:
 * turn the orchestrator on, fix the file's header, or read the message.
 */
function UploadFailure({ error }: { error: unknown }) {
  if (isPipelineOffline(error)) {
    return (
      <div className="empty">
        <p className="empty__title">The pipeline service is not running</p>
        <p className="empty__body">
          Ingestion is orchestrated by Airflow, which lives behind an opt-in compose profile
          because it is the heaviest service in the stack. Start it from the repo root:
        </p>
        <pre className="code code--inline">make pipeline-up</pre>
        <p className="empty__body">
          That brings up Airflow on <a href="http://localhost:8081">localhost:8081</a> (admin /
          admin). Everything else on this dashboard works without it.
        </p>
        <p className="empty__detail">{(error as Error).message}</p>
      </div>
    )
  }

  if (isHeaderMismatch(error)) {
    const { detail } = error
    return (
      <div className="empty">
        <p className="empty__title">That file&apos;s header does not match the table</p>
        <dl className="ingest__diff">
          {detail.missing.length > 0 && (
            <>
              <dt>Missing</dt>
              <dd className="mono">{detail.missing.join(', ')}</dd>
            </>
          )}
          {detail.unexpected.length > 0 && (
            <>
              <dt>Unexpected</dt>
              <dd className="mono">{detail.unexpected.join(', ')}</dd>
            </>
          )}
          <dt>Expected</dt>
          <dd className="mono">{detail.expected.join(', ')}</dd>
          <dt>Found</dt>
          <dd className="mono">{detail.found.join(', ') || '(nothing)'}</dd>
        </dl>
        <p className="empty__body">
          Column order and case do not matter — the names do. If the file is pipe-delimited, name
          it <span className="mono">.psv</span> so the delimiter is inferred correctly.
        </p>
      </div>
    )
  }

  return (
    <p className="state state--error" role="alert">
      <strong>Upload failed.</strong> {(error as Error).message}
    </p>
  )
}
