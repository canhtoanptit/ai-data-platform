import { NavLink } from 'react-router-dom'

import { HealthIndicator } from './HealthIndicator'

/** The four pages, in the order the platform is built up. */
const PAGES: ReadonlyArray<{ to: string; label: string }> = [
  { to: '/', label: 'Dashboard' },
  { to: '/catalog', label: 'Catalog' },
  { to: '/lineage', label: 'Lineage' },
  { to: '/runs', label: 'Runs' },
]

export function Header() {
  return (
    <header className="header">
      <div className="header__inner">
        <div>
          <h1 className="header__title">AI Data Platform</h1>
          <p className="header__subtitle">
            Collections marts, built by dbt and served over FastAPI
          </p>
        </div>
        <HealthIndicator />
      </div>
      <nav className="nav" aria-label="Sections">
        <div className="nav__inner">
          {PAGES.map((page) => (
            <NavLink
              key={page.to}
              to={page.to}
              // `end` only on the index route: without it, "/" would count as a
              // prefix of every other path and stay highlighted everywhere.
              end={page.to === '/'}
              className={({ isActive }) => (isActive ? 'nav__link nav__link--active' : 'nav__link')}
            >
              {page.label}
            </NavLink>
          ))}
        </div>
      </nav>
    </header>
  )
}
