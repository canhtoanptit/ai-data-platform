import { AmountByBucketChart } from './components/AmountByBucketChart'
import { CasesTable } from './components/CasesTable'
import { CureRateByTeamChart } from './components/CureRateByTeamChart'
import { Header } from './components/Header'
import { KpiTilesSection } from './components/KpiTiles'
import { Panel } from './components/Panel'

export default function App() {
  return (
    <>
      <Header />
      <main className="container">
        <Panel title="Portfolio" subtitle="Every case in fct_collection_cases">
          <KpiTilesSection />
        </Panel>

        <div className="chart-grid">
          <Panel title="Delinquent amount by bucket" subtitle="Grouped by collections team">
            <AmountByBucketChart />
          </Panel>
          <Panel title="Cure rate by team" subtitle="Cured cases / total cases">
            <CureRateByTeamChart />
          </Panel>
        </div>

        <CasesTable />
      </main>
    </>
  )
}
