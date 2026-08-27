import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  /** One line under the title: what the numbers mean, or where they come from. */
  subtitle?: string
  /** Controls rendered on the title row, e.g. a filter dropdown. */
  actions?: ReactNode
  children: ReactNode
}

/** A titled card. Every section on the page is one of these. */
export function Panel({ title, subtitle, actions, children }: PanelProps) {
  return (
    <section className="panel">
      <div className="panel__head">
        <div>
          <h2 className="panel__title">{title}</h2>
          {subtitle && <p className="panel__subtitle">{subtitle}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  )
}
