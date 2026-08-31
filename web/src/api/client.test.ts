import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, askChat, isSqlRejected } from './client'

/**
 * Tests for the client's only POST. Everything else in client.ts is a thin
 * `fetch(path)`, but `askChat` has three things worth pinning down: the request
 * it serialises, and the two error bodies the chat endpoint answers with (a
 * string `detail` for 503/429, a structured one for 422).
 */

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, ...response })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('askChat', () => {
  it('POSTs the question as JSON to /api/chat', async () => {
    const fetchMock = mockFetch({ json: async () => ({ question: 'hi', rows: [] }) })

    await askChat('Which team has the highest cure rate?')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    // Relative path, so whichever server served the app proxies it — Vite in
    // dev, nginx in the container.
    expect(path).toBe('/api/chat')
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
    expect(init.body).toBe(
      JSON.stringify({ question: 'Which team has the highest cure rate?' }),
    )
  })

  it('turns a 503 into an ApiError carrying the setup instructions', async () => {
    mockFetch({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
      json: async () => ({ detail: 'this server has no GROQ_API_KEY' }),
    })

    // The API's own `detail` is far more useful on screen than the status line,
    // so it becomes the Error's message.
    await expect(askChat('anything?')).rejects.toMatchObject({
      name: 'ApiError',
      status: 503,
      message: 'this server has no GROQ_API_KEY',
    })
  })

  it('keeps the structured 422 detail so the UI can show the attempted SQL', async () => {
    mockFetch({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({
        detail: {
          message: 'The generated SQL was rejected by the warehouse.',
          sql: 'SELECT nope FROM analytics_marts.dim_agents',
          error: 'column "nope" does not exist',
        },
      }),
    })

    const error = await askChat('anything?').catch((thrown: unknown) => thrown)

    expect(isSqlRejected(error)).toBe(true)
    if (!isSqlRejected(error)) throw new Error('unreachable')
    // `message` is lifted out of the object so generic error rendering still
    // says something, and the rest is kept for the SQL block.
    expect(error.message).toBe('The generated SQL was rejected by the warehouse.')
    expect(error.detail.sql).toContain('SELECT nope')
    expect(error.detail.error).toBe('column "nope" does not exist')
  })

  it('falls back to the status line when the body is not the JSON we expect', async () => {
    // A proxy error page, say: nginx answering before FastAPI is reached.
    mockFetch({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      json: async () => {
        throw new Error('not json')
      },
    })

    await expect(askChat('anything?')).rejects.toThrow('POST /api/chat failed: 502 Bad Gateway')
  })
})

describe('isSqlRejected', () => {
  it('is false for a plain-string error body and for non-ApiErrors', () => {
    expect(isSqlRejected(new ApiError(503, 'no key'))).toBe(false)
    expect(isSqlRejected(new Error('offline'))).toBe(false)
    expect(isSqlRejected(undefined)).toBe(false)
  })
})
