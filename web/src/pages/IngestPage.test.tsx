import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { IngestAccepted, IngestRunStatus, IngestTable } from '../api/types'
import { IngestPage } from './IngestPage'

/**
 * Page-level wiring in jsdom: pick a table, choose a file, upload, watch the run.
 *
 * The page talks to three endpoints, so `fetch` is stubbed with a router on the
 * URL rather than a single canned response — the tests are then written in terms
 * of "what the API said", which is the only thing this component actually
 * depends on.
 *
 * Two things jsdom cannot do, and what is done instead:
 *  - a real drag-and-drop: the file input and the drop handler feed the *same*
 *    state, so exercising the input covers the interesting half, and a
 *    `fireEvent.drop` with a synthetic dataTransfer covers the other;
 *  - real timers for the poll: the run states are asserted one response at a
 *    time instead, which is what the panel renders from anyway.
 */

const TABLES: IngestTable[] = [
  {
    name: 'raw_payments',
    label: 'Payments',
    staging_model: 'stg_collections__payments',
    columns: ['payment_id', 'account_id', 'payment_date', 'amount', 'method'],
  },
  {
    name: 'raw_accounts',
    label: 'Accounts',
    staging_model: 'stg_collections__accounts',
    columns: ['account_id', 'customer_id', 'product_type'],
  },
]

const ACCEPTED: IngestAccepted = {
  dag_run_id: 'ingest__20260903T050541__f69d0cfb',
  dag_id: 'file_ingest',
  table: 'raw_payments',
  filename: 'ingest__20260903T050541__f69d0cfb__new_payments.csv',
  poll: '/api/ingest/runs/ingest__20260903T050541__f69d0cfb',
}

function runStatus(overrides: Partial<IngestRunStatus> = {}): IngestRunStatus {
  return {
    dag_run_id: ACCEPTED.dag_run_id,
    state: 'success',
    is_running: false,
    started_at: '2026-09-03T05:05:43+00:00',
    ended_at: '2026-09-03T05:05:50+00:00',
    tasks: [
      {
        task_id: 'copy_into_raw',
        state: 'success',
        duration_seconds: 0.14,
        started_at: null,
        ended_at: null,
      },
      {
        task_id: 'dbt_build_downstream',
        state: 'success',
        duration_seconds: 3.69,
        started_at: null,
        ended_at: null,
      },
    ],
    ...overrides,
  }
}

interface StubResponse {
  ok: boolean
  status: number
  body: unknown
}

/**
 * Stub `fetch`, routed by URL. `upload` and `run` are optional so a test that
 * only cares about the form does not have to describe responses it never asks
 * for.
 */
function stubApi(routes: {
  tables?: StubResponse
  upload?: StubResponse
  run?: StubResponse
}) {
  const ok = (body: unknown): StubResponse => ({ ok: true, status: 200, body })
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const chosen =
      init?.method === 'POST'
        ? (routes.upload ?? ok(ACCEPTED))
        : url.includes('/runs/')
          ? (routes.run ?? ok(runStatus()))
          : (routes.tables ?? ok(TABLES))
    return Promise.resolve({
      ok: chosen.ok,
      status: chosen.status,
      statusText: 'stub',
      json: async () => chosen.body,
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function wrap(children: ReactNode) {
  // A fresh QueryClient per test so no cache leaks between them, and retries off
  // so an error state is asserted immediately rather than after three attempts.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

const csvFile = () =>
  new File(['payment_id,account_id\n9016,5001\n'], 'new_payments.csv', { type: 'text/csv' })

const fileInput = () => screen.getByLabelText('choose a file') as HTMLInputElement
const uploadButton = () => screen.getByRole('button', { name: /Upload and run/ })

async function chooseFileAndUpload() {
  await waitFor(() => expect(uploadButton()).toBeInTheDocument())
  fireEvent.change(fileInput(), { target: { files: [csvFile()] } })
  fireEvent.click(uploadButton())
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('IngestPage upload form', () => {
  it('lists the destination tables and their expected header', async () => {
    stubApi({})
    render(wrap(<IngestPage />))

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: 'Destination table' })).toBeInTheDocument()
    })
    expect(screen.getByRole('option', { name: /Payments — raw_payments/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Accounts — raw_accounts/ })).toBeInTheDocument()
    // The header hint is what stops a user guessing at the file format.
    expect(
      screen.getByText('payment_id, account_id, payment_date, amount, method'),
    ).toBeInTheDocument()
  })

  it('will not upload before a file is chosen', async () => {
    const fetchMock = stubApi({})
    render(wrap(<IngestPage />))

    await waitFor(() => expect(uploadButton()).toBeInTheDocument())
    expect(uploadButton()).toBeDisabled()

    fireEvent.click(uploadButton())
    // Only the tables request; nothing was posted.
    expect(fetchMock.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
  })

  it('posts the chosen table and file as multipart form data', async () => {
    const fetchMock = stubApi({})
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true)
    })
    const [url, init] = fetchMock.mock.calls.find(
      ([, candidate]) => candidate?.method === 'POST',
    ) as [string, RequestInit]

    expect(url).toBe('/api/ingest')
    const form = init.body as FormData
    expect(form.get('table')).toBe('raw_payments')
    expect((form.get('file') as File).name).toBe('new_payments.csv')
    // No Content-Type of our own: fetch has to add the multipart boundary.
    expect(init.headers).toBeUndefined()
  })

  it('accepts a dropped file as well as a picked one', async () => {
    stubApi({})
    render(wrap(<IngestPage />))
    await waitFor(() => expect(uploadButton()).toBeInTheDocument())

    fireEvent.drop(screen.getByText('Drag a file here, or'), {
      dataTransfer: { files: [csvFile()] },
    })

    await waitFor(() => expect(uploadButton()).toBeEnabled())
    // Scoped to the "chosen file" line by class: the page also links a *sample*
    // named new_payments.csv, so a bare text query matches two things.
    expect(document.querySelector('.ingest__chosen')).toHaveTextContent('new_payments.csv')
  })

  it('shows the column diff when the header does not match', async () => {
    stubApi({
      upload: {
        ok: false,
        status: 422,
        body: {
          detail: {
            message: 'The file\'s header does not match raw_payments. Missing: payment_date.',
            expected: ['account_id', 'amount', 'method', 'payment_date', 'payment_id'],
            found: ['payment_id', 'account_id', 'paid_on', 'amount', 'method'],
            missing: ['payment_date'],
            unexpected: ['paid_on'],
          },
        },
      },
    })
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => {
      expect(screen.getByText(/header does not match the table/)).toBeInTheDocument()
    })
    // The useful half of "invalid file" is *which* columns.
    expect(screen.getByText('payment_date')).toBeInTheDocument()
    expect(screen.getByText('paid_on')).toBeInTheDocument()
  })

  it('renders the pipeline-profile-off state with the make target', async () => {
    stubApi({
      upload: {
        ok: false,
        status: 503,
        body: {
          detail:
            'Airflow is not reachable at http://airflow:8080 (ConnectError) — '
            + 'start the pipeline profile: make pipeline-up',
        },
      },
    })
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => {
      expect(screen.getByText('The pipeline service is not running')).toBeInTheDocument()
    })
    // The instruction, not a stack trace: this is a setup state, not a fault.
    expect(screen.getByText('make pipeline-up')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports any other failure as an error', async () => {
    stubApi({
      upload: { ok: false, status: 413, body: { detail: 'File is larger than the 5 MB limit.' } },
    })
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('File is larger than the 5 MB limit.')
    })
  })
})

describe('IngestPage run panel', () => {
  it('shows both tasks green and links onward on success', async () => {
    stubApi({})
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => {
      expect(screen.getByText('Loaded, transformed and tested')).toBeInTheDocument()
    })
    expect(screen.getByText('copy_into_raw')).toBeInTheDocument()
    expect(screen.getByText('dbt_build_downstream')).toBeInTheDocument()
    expect(screen.getByText('took 3.69s')).toBeInTheDocument()
    // Where to look next, only once the run has settled.
    expect(screen.getByRole('link', { name: /See the dbt run/ })).toHaveAttribute('href', '/runs')
    expect(screen.getByRole('link', { name: /Back to the dashboard/ })).toHaveAttribute(
      'href',
      '/',
    )
  })

  it('frames a failed run as the quality gate, not a broken page', async () => {
    stubApi({
      run: {
        ok: true,
        status: 200,
        body: runStatus({
          state: 'failed',
          tasks: [
            {
              task_id: 'copy_into_raw',
              state: 'success',
              duration_seconds: 0.14,
              started_at: null,
              ended_at: null,
            },
            {
              task_id: 'dbt_build_downstream',
              state: 'failed',
              duration_seconds: 2.9,
              started_at: null,
              ended_at: null,
            },
          ],
        }),
      },
    })
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => {
      expect(screen.getByText('The pipeline stopped')).toBeInTheDocument()
    })
    expect(screen.getByText(/quality gate working/)).toBeInTheDocument()
    // The COPY succeeded and dbt did not: both facts are on screen, because
    // "which task" is the first thing anyone asks.
    expect(screen.getAllByText('success')).toHaveLength(1)
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('keeps the panel neutral while the run is still going', async () => {
    stubApi({
      run: {
        ok: true,
        status: 200,
        body: runStatus({
          state: 'running',
          is_running: true,
          ended_at: null,
          tasks: [
            {
              task_id: 'copy_into_raw',
              state: 'success',
              duration_seconds: 0.14,
              started_at: null,
              ended_at: null,
            },
            {
              task_id: 'dbt_build_downstream',
              state: 'running',
              duration_seconds: null,
              started_at: null,
              ended_at: null,
            },
          ],
        }),
      },
    })
    render(wrap(<IngestPage />))
    await chooseFileAndUpload()

    await waitFor(() => expect(screen.getByText('Running')).toBeInTheDocument())
    expect(screen.getByText('running')).toBeInTheDocument()
    // "See the numbers move" would point at numbers that have not moved yet.
    expect(screen.queryByRole('link', { name: /See the dbt run/ })).not.toBeInTheDocument()
  })
})

describe('IngestPage explainer', () => {
  it('documents the duplicate-upload failure as intentional', async () => {
    stubApi({})
    render(wrap(<IngestPage />))

    // The explainer renders outside QueryState, so it is there immediately —
    // the sample links are not, so wait on the form before asserting them.
    expect(screen.getByText(/Upload the same file twice on purpose/)).toBeInTheDocument()
    await waitFor(() => expect(uploadButton()).toBeInTheDocument())

    // The two sample files are the fastest way to try it.
    expect(screen.getByRole('link', { name: 'new_payments.csv' })).toHaveAttribute(
      'href',
      '/samples/new_payments.csv',
    )
    expect(screen.getByRole('link', { name: 'new_payments.psv' })).toHaveAttribute(
      'href',
      '/samples/new_payments.psv',
    )
  })
})
