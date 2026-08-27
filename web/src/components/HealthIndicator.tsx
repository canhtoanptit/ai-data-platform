import { useQuery } from '@tanstack/react-query'

import { getHealth } from '../api/client'

/** How often to re-check. Long enough to be free, short enough to notice. */
const POLL_MS = 30_000

/** Live status of the API and the warehouse behind it. */
export function HealthIndicator() {
  const query = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    // react-query keeps polling in the background; nothing else needs to know.
    refetchInterval: POLL_MS,
    retry: false,
  })

  // /api/health answers 200 even when the warehouse is unreachable, so there are
  // three outcomes, not two: the request failed (API down), the request
  // succeeded but the db check failed, or all clear.
  let tone = 'pending'
  let text = 'Checking…'
  if (query.isError) {
    tone = 'down'
    text = 'API unreachable'
  } else if (query.data) {
    const dbOk = query.data.database === 'ok'
    tone = dbOk ? 'ok' : 'down'
    text = dbOk ? 'API + warehouse ok' : 'Warehouse unreachable'
  }

  return (
    <p className={`health health--${tone}`}>
      {/* The dot is decoration; the text carries the meaning, so status is never
          communicated by colour alone. */}
      <span className="health__dot" aria-hidden="true" />
      {text}
    </p>
  )
}
