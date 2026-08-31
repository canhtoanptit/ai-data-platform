import { useRef, useState } from 'react'

import { askChat } from '../api/client'
import type { ChatTurn } from '../components/ChatThread'
import { ChatThread } from '../components/ChatThread'
import { Panel } from '../components/Panel'

/**
 * Starters, so the empty page is not a blank prompt. Chosen to show the three
 * shapes the marts answer well: a ranking, a grouped total, and a filtered row
 * list.
 */
const EXAMPLES = [
  'Which team has the highest cure rate?',
  'Total delinquent amount by delinquency bucket?',
  'Which customers have open cases over $500?',
  'How many cases were written off, by product type?',
] as const

/** Matches the API's `question` field (1..500 chars). */
const MAX_QUESTION = 500

/**
 * Ask the marts a question in English.
 *
 * The API does the work: an LLM writes one PostgreSQL SELECT against a schema
 * briefing generated from dbt's own metadata, a parser validates it, and it runs
 * in a read-only transaction. This page is the transcript plus an input.
 *
 * **Conversation memory is a deliberate non-goal for v1.** The endpoint is
 * single-turn — every question is answered from the schema alone, with no
 * history — so follow-ups like "and by team?" will not work. The transcript
 * below is therefore purely a local display of past exchanges, held in this
 * component and gone on reload. Adding memory means sending prior turns to the
 * model and deciding what to do when the context fills; the interesting problem
 * here is the SQL, not the dialogue.
 *
 * Hence `useState` and a plain async call rather than TanStack Query, which the
 * rest of the app uses: asking a question is an action with a one-off result,
 * not shared server state worth caching under a key.
 */
export function ChatPage() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  // A ref, not Date.now(): two questions asked in the same millisecond would
  // share an id, and the reply would be written into the wrong turn.
  const nextId = useRef(0)

  // One in flight at a time: the answers are appended in order, and a
  // rate-limited free tier is not the place to fire off parallel requests.
  const pending = turns.some((turn) => turn.status === 'pending')

  async function ask(question: string) {
    const trimmed = question.trim()
    if (trimmed === '' || pending) return

    nextId.current += 1
    const id = nextId.current
    setTurns((previous) => [...previous, { id, question: trimmed, status: 'pending' }])
    setDraft('')

    // Replace by id rather than by index: the array is only appended to, but an
    // id-keyed update stays correct if that ever changes.
    const settle = (turn: ChatTurn) =>
      setTurns((previous) => previous.map((existing) => (existing.id === id ? turn : existing)))

    try {
      settle({ id, question: trimmed, status: 'answered', answer: await askChat(trimmed) })
    } catch (error) {
      // Every failure mode (no API key, throttled, unusable SQL) is rendered in
      // the thread by ChatThread, so nothing is swallowed and nothing throws
      // out of the page.
      settle({ id, question: trimmed, status: 'failed', error })
    }
  }

  return (
    <Panel
      title="Ask AI"
      subtitle="Natural language → SQL over the marts, executed read-only"
    >
      {turns.length === 0 ? (
        <div className="chat__examples">
          <p className="chat__examples-title">Try one of these:</p>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="chat__chip"
              onClick={() => void ask(example)}
              disabled={pending}
            >
              {example}
            </button>
          ))}
        </div>
      ) : (
        <ChatThread turns={turns} />
      )}

      <form
        className="chat__form"
        onSubmit={(event) => {
          // A single-line input submits on Enter for free once it is in a form,
          // which is the behaviour a chat box needs — no key handler required.
          event.preventDefault()
          void ask(draft)
        }}
      >
        <input
          type="text"
          className="search chat__input"
          placeholder="Which team has the highest cure rate?"
          aria-label="Ask a question about the collections data"
          value={draft}
          maxLength={MAX_QUESTION}
          disabled={pending}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="chat__send" disabled={pending || draft.trim() === ''}>
          {pending ? 'Asking…' : 'Ask'}
        </button>
      </form>
    </Panel>
  )
}
