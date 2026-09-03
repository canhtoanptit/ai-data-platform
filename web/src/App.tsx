import { Route, Routes } from 'react-router-dom'

import { Header } from './components/Header'
import { CatalogPage } from './pages/CatalogPage'
import { ChatPage } from './pages/ChatPage'
import { DashboardPage } from './pages/DashboardPage'
import { IngestPage } from './pages/IngestPage'
import { LineagePage } from './pages/LineagePage'
import { RunsPage } from './pages/RunsPage'

/**
 * Six pages over three data sources: the dashboard reads the marts,
 * catalog/lineage/runs read the artifacts dbt wrote while building them, the
 * chat page uses both — dbt's metadata as the schema an LLM writes SQL against,
 * the warehouse to run it — and the ingest page reads Airflow, because it is the
 * one page that starts a pipeline rather than reading its output.
 *
 * Deep links work in both serving modes — Vite's dev server and the nginx image
 * both fall back to index.html for unknown paths (`try_files` in nginx.conf), so
 * a refresh on /lineage loads the app rather than 404ing.
 */
export default function App() {
  return (
    <>
      <Header />
      <main className="container">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="/lineage" element={<LineagePage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/ingest" element={<IngestPage />} />
          {/* Anything else lands on the dashboard rather than a blank page. */}
          <Route path="*" element={<DashboardPage />} />
        </Routes>
      </main>
    </>
  )
}
