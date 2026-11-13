import { useEffect, useState } from 'react'
import WildfireMap from './WildfireMap.tsx'

type Dataset = {
  name: string
  file: string
  status: string
  row_count: number
  columns: string[]
  acquisition_date_range: { start: string | null; end: string | null }
}

type Summary = {
  data_status: string
  provenance_note: string
  datasets: Record<string, Dataset>
}

const API = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

function formatDateRange(dataset: Dataset) {
  const { start, end } = dataset.acquisition_date_range
  if (!start || !end) return 'Date range unavailable'
  return start === end ? start : `${start} — ${end}`
}

function App() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch(`${API}/api/datasets/summary`)
      .then((response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`)
        return response.json() as Promise<Summary>
      })
      .then(setSummary)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Could not reach the API'))
  }, [])

  const datasets = summary ? Object.values(summary.datasets) : []
  const totalRows = datasets.reduce((total, dataset) => total + dataset.row_count, 0)

  return (
    <main className="shell">
      <aside className="rail" aria-label="Primary navigation">
        <div className="brand-mark">F<span>·</span></div>
        <div className="rail-divider" />
        <button className="rail-button active" aria-label="Overview" title="Overview">⌂</button>
        <button className="rail-button" aria-label="Datasets" title="Datasets">▤</button>
        <div className="rail-bottom"><span className="pulse" />LOCAL</div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="wordmark">FIRESYNC <span>FIELD INTELLIGENCE</span></div>
          <div className="topbar-right"><span className="status-dot" /> FOUNDATION / LOCAL</div>
        </header>

        <div className="content">
          <div className="eyebrow"><span className="eyebrow-line" /> OBSERVATION DESK <span className="eyebrow-index">01 / OVERVIEW</span></div>
          <section className="intro">
            <div>
              <h1>Fire activity<br /><em>at a glance.</em></h1>
              <p className="lede">A read-only workspace for exploring local satellite fire observation files.</p>
            </div>
            <div className="intro-stamp"><span>DATA STATUS</span><strong>UNVERIFIED<br />LOCAL DATA</strong><small>Source confirmation pending</small></div>
          </section>

          <div className="notice"><span className="notice-icon">!</span><p><strong>Provenance not confirmed.</strong> These files are not represented as authenticated NASA observations. Confirm source records before using them for scientific or operational claims.</p></div>

          <section className="metrics" aria-label="Dataset summary">
            <article className="metric-card">
              <span className="metric-label">LOCAL RECORDS</span>
              <strong>{summary ? totalRows.toLocaleString() : '—'}</strong>
              <span className="metric-foot">Across {datasets.length || '—'} source files</span>
            </article>
            <article className="metric-card">
              <span className="metric-label">DATASETS</span>
              <strong>{summary ? datasets.length.toString().padStart(2, '0') : '—'}</strong>
              <span className="metric-foot">MODIS + VIIRS local CSVs</span>
            </article>
            <article className="metric-card metric-card-wide">
              <span className="metric-label">APPLICATION MODE</span>
              <strong className="mode-value">READ ONLY <span className="mode-dot" /></strong>
              <span className="metric-foot">No harmonization or analysis applied</span>
            </article>
          </section>

          <section className="section-heading">
            <div><span className="section-kicker">01—02 / CATALOG</span><h2>Local datasets</h2></div>
            <span className="catalog-meta">LOCAL FILE SUMMARY <span className="mini-pulse" /></span>
          </section>

          {error && <div className="error-panel">API connection unavailable: {error}. Start the backend to load file summaries.</div>}
          <section className="dataset-grid" aria-label="Local datasets">
            {datasets.map((dataset, index) => (
              <article className="dataset-card" key={dataset.name}>
                <div className="dataset-top"><span className="dataset-index">0{index + 1} / SENSOR FILE</span><span className="dataset-status">UNVERIFIED LOCAL DATA</span></div>
                <div className="dataset-title-row"><h3>{dataset.name}</h3><span className="satellite-glyph">◉</span></div>
                <div className="dataset-file">{dataset.file}</div>
                <div className="dataset-rule" />
                <div className="dataset-stats"><div><span>RECORDS</span><strong>{dataset.row_count.toLocaleString()}</strong></div><div><span>ACQUISITION WINDOW</span><strong className="date-value">{formatDateRange(dataset)}</strong></div></div>
                <div className="field-list"><span>AVAILABLE FIELDS</span><p>{dataset.columns.join(' · ')}</p></div>
              </article>
            ))}
            {!summary && !error && <div className="loading-card">Connecting to local data service<span className="loading-pulse" /></div>}
          </section>

          <WildfireMap />

          <footer><span>FIRESYNC / APPLICATION FOUNDATION</span><span>LOCAL FILES · READ ONLY · PROVENANCE UNVERIFIED</span></footer>
        </div>
      </section>
    </main>
  )
}

export default App
