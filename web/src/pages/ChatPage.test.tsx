import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ChatAnswer } from '../api/types'
import { ChatPage } from './ChatPage'

/**
 * Page-level wiring, in jsdom: chip → request → reply in the thread. ChatThread
 * has its own tests for how a reply *looks*; this one covers the parts only the
 * page owns — the transcript state, the input clearing, and that a failure lands
 * in the thread instead of throwing out of the page.
 *
 * `fireEvent` rather than user-event, which is not a dependency here: these are
 * single clicks and a form submit, not interaction sequences that need a real
 * event simulation.
 */

const ANSWER: ChatAnswer = {
  question: 'Which team has the highest cure rate?',
  sql: 'SELECT team FROM analytics_marts.collections_performance LIMIT 100',
  columns: ['team'],
  rows: [['early_stage']],
  row_count: 1,
  truncated: false,
  answer: 'The early_stage team leads at 100%.',
  model: 'llama-3.3-70b-versatile',
}

const EXAMPLE = 'Which team has the highest cure rate?'

function stubFetch(response: { ok: boolean; status: number; body: unknown }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status,
    statusText: 'stub',
    json: async () => response.body,
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const input = () =>
  screen.getByRole('textbox', { name: 'Ask a question about the collections data' })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatPage', () => {
  it('offers example questions when the thread is empty', () => {
    stubFetch({ ok: true, status: 200, body: ANSWER })
    render(<ChatPage />)

    expect(screen.getByText('Try one of these:')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: EXAMPLE })).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('asks the clicked example and shows the reply in the thread', async () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: ANSWER })
    render(<ChatPage />)

    fireEvent.click(screen.getByRole('button', { name: EXAMPLE }))

    await waitFor(() => {
      expect(screen.getByText('The early_stage team leads at 100%.')).toBeInTheDocument()
    })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.body).toBe(JSON.stringify({ question: EXAMPLE }))
    // The chips are replaced by the transcript once there is one.
    expect(screen.queryByText('Try one of these:')).not.toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('sends the typed question on submit and clears the box', async () => {
    // Submitting the form is what pressing Enter in a single-line input does in
    // a browser; jsdom does not implement that implicit submission itself.
    stubFetch({ ok: true, status: 200, body: ANSWER })
    render(<ChatPage />)

    fireEvent.change(input(), { target: { value: 'how many open cases?' } })
    fireEvent.submit(input().closest('form')!)

    await waitFor(() => {
      expect(screen.getByText('how many open cases?')).toBeInTheDocument()
    })
    expect(input()).toHaveValue('')
  })

  it('will not send an empty question', () => {
    const fetchMock = stubFetch({ ok: true, status: 200, body: ANSWER })
    render(<ChatPage />)

    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    fireEvent.submit(input().closest('form')!)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('tells a spent token budget apart from a per-minute rate limit', async () => {
    // Both are 429 and both are *expected states* of a working system, so
    // neither is styled as an error — but the answer to "what now?" differs
    // (tomorrow vs a minute), so the copy has to differ too.
    stubFetch({
      ok: false,
      status: 429,
      body: {
        detail:
          'The daily LLM token budget is exhausted (200,000/200,000 used); it '
          + 'resets at midnight UTC.',
      },
    })
    render(<ChatPage />)

    fireEvent.click(screen.getByRole('button', { name: EXAMPLE }))

    await waitFor(() => {
      expect(screen.getByText(/AI budget is spent/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/per-minute limit/)).not.toBeInTheDocument()
    // Not an alert: nothing is broken, so nothing should be announced as broken.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders a rate-limit 429 as a wait-a-moment note', async () => {
    stubFetch({
      ok: false,
      status: 429,
      body: { detail: 'Too many questions from this address (10 per 1 minute).' },
    })
    render(<ChatPage />)

    fireEvent.click(screen.getByRole('button', { name: EXAMPLE }))

    await waitFor(() => {
      expect(screen.getByText(/per-minute limit/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/AI budget is spent/)).not.toBeInTheDocument()
  })

  it('renders a failure in the thread rather than throwing', async () => {
    stubFetch({
      ok: false,
      status: 503,
      body: { detail: 'this server has no GROQ_API_KEY — console.groq.com' },
    })
    render(<ChatPage />)

    fireEvent.click(screen.getByRole('button', { name: EXAMPLE }))

    await waitFor(() => {
      expect(screen.getByText('AI chat needs a free API key')).toBeInTheDocument()
    })
    // Still askable afterwards: the failure belongs to that turn, not the page.
    expect(input()).toBeEnabled()
  })
})
