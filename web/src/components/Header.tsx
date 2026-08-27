import { HealthIndicator } from './HealthIndicator'

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
    </header>
  )
}
