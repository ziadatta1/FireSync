import { useCallback, useEffect, useMemo, useState } from 'react'
import GlobalMap from './GlobalMap.tsx'
import AnalyticsPanel from './AnalyticsPanel.tsx'
import ComparePanel from './ComparePanel.tsx'
import DataPanel from './DataPanel.tsx'
import Methodology from './Methodology.tsx'
import HistoricalPanel from './HistoricalPanel.tsx'
import { SOURCES, SOURCE_IDS, fmt, getJson, type Area, type Filters, type GlobalDataset, type SourceId } from './api.ts'

type Tab = 'map' | 'compare' | 'history' | 'data' | 'method'
const TABS: { id: Tab; icon: string; label: string }[] = [
  { id: 'map', icon: '◍', label: 'Global map & area analysis' },
  { id: 'compare', icon: '⇄', label: 'Sensor comparison' },
  { id: 'history', icon: '▦', label: 'Historical calendar' },
  { id: 'data', icon: '▤', label: 'Data & provenance' },
  { id: 'method', icon: '∑', label: 'Methodology' },
]

function AreaInput({ area, onArea }: { area: Area | null; onArea: (a: Area | null) => void }) {
  const [draft, setDraft] = useState({ south: '', west: '', north: '', east: '' })
  const [error, setError] = useState('')
  useEffect(() => {
    if (area?.kind === 'bbox') setDraft({ south: area.south.toFixed(2), west: area.west.toFixed(2), north: area.north.toFixed(2), east: area.east.toFixed(2) })
  }, [area])
  const apply = () => {
    const v = Object.fromEntries(Object.entries(draft).map(([k, x]) => [k, Number(x)])) as Record<'south' | 'west' | 'north' | 'east', number>
    if (Object.values(draft).some((x) => x.trim() === '') || Object.values(v).some(Number.isNaN)) return setError('Enter four numbers.')
    if (!(v.south >= -90 && v.north <= 90 && v.south < v.north)) return setError('Need −90 ≤ south < north ≤ 90.')
    if (!(v.west >= -180 && v.east <= 180 && v.west < v.east)) return setError('Need −180 ≤ west < east ≤ 180 (no dateline crossing).')
    setError('')
    onArea({ kind: 'bbox', ...v })
  }
  return (
    <form className="area-input" onSubmit={(e) => { e.preventDefault(); apply() }}>
      <span className="control-label">COORDINATES</span>
      {(['south', 'west', 'north', 'east'] as const).map((k) => (
        <label key={k}><span>{k.toUpperCase()}</span><input inputMode="decimal" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} placeholder={k === 'south' || k === 'north' ? 'lat' : 'lon'} /></label>
      ))}
      <button type="submit" className="ghost-button">Set area</button>
      {error && <span className="input-error">{error}</span>}
    </form>
  )
}

function App() {
  const [tab, setTab] = useState<Tab>('map')
  const [datasets, setDatasets] = useState<GlobalDataset[]>([])
  const [catalogError, setCatalogError] = useState('')
  const [filters, setFilters] = useState<Filters>({ sources: [...SOURCE_IDS], dateFrom: '', dateTo: '', minConfidence: '', daynight: '' })
  const [area, setArea] = useState<Area | null>(null)
  const [focus, setFocus] = useState<{ lat: number; lon: number; zoom: number; n: number } | null>(null)

  useEffect(() => {
    getJson<{ datasets: GlobalDataset[] }>('/api/global/datasets')
      .then((body) => setDatasets(body.datasets))
      .catch((e: unknown) => setCatalogError(e instanceof Error ? e.message : 'Could not reach the API'))
  }, [])

  const dates = useMemo(() => [...new Set(datasets.flatMap((d) => Object.keys(d.acquisition_dates)))].sort(), [datasets])
  const totalRows = datasets.reduce((s, d) => s + d.row_count, 0)
  const toggle = (s: SourceId) => setFilters((f) => ({ ...f, sources: f.sources.includes(s) ? f.sources.filter((x) => x !== s) : SOURCE_IDS.filter((x) => x === s || f.sources.includes(x)) }))
  const onFocus = useCallback((lat: number, lon: number) => { setTab('map'); setFocus({ lat, lon, zoom: 7, n: Date.now() }); window.scrollTo({ top: 0, behavior: 'smooth' }) }, [])

  return (
    <main className="shell">
      <aside className="rail" aria-label="Primary navigation">
        <div className="brand-mark">F<span>·</span></div>
        <div className="rail-divider" />
        {TABS.map((t) => (
          <button key={t.id} className={`rail-button ${tab === t.id ? 'active' : ''}`} aria-label={t.label} title={t.label} onClick={() => setTab(t.id)} aria-current={tab === t.id ? 'page' : undefined}>{t.icon}</button>
        ))}
        <div className="rail-bottom"><span className="pulse" />FIRMS</div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="wordmark">FIRESYNC <span>MULTI-SATELLITE ACTIVE FIRE MONITOR</span></div>
          <nav className="tabs" aria-label="Views">
            {TABS.map((t) => <button key={t.id} type="button" className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>{t.label}</button>)}
          </nav>
          <div className="topbar-right"><span className="status-dot" /> {datasets.length ? `${fmt(totalRows)} NASA FIRMS DETECTIONS · ${dates[0]} → ${dates[dates.length - 1]}` : catalogError ? 'API OFFLINE' : 'CONNECTING…'}</div>
        </header>

        <div className="content">
          {catalogError && <div className="error-panel">API connection unavailable: {catalogError}. Start the backend (see README) to load data.</div>}
          {tab !== 'data' && tab !== 'method' && tab !== 'history' && (
            <div className="filter-bar">
              {tab === 'map' && <div className="layer-toggles" aria-label="Satellite sources">
                <span className="control-label">SOURCES</span>
                {SOURCE_IDS.map((s) => (
                  <label key={s} className={`layer-toggle ${filters.sources.includes(s) ? 'checked' : ''}`}>
                    <input type="checkbox" checked={filters.sources.includes(s)} onChange={() => toggle(s)} />
                    <i className={`shape-key ${SOURCES[s].shape}`} style={{ background: SOURCES[s].color }} />{SOURCES[s].short}
                  </label>
                ))}
              </div>}
              {tab === 'compare' && <span className="control-label">FILTERS APPLIED TO BOTH SENSORS</span>}
              <div className="filter-fields compact">
                <label><span>FROM (UTC)</span><select value={filters.dateFrom} onChange={(e) => setFilters({ ...filters, dateFrom: e.target.value })}><option value="">{dates[0] ?? 'first'}</option>{dates.map((d) => <option key={d} value={d}>{d}</option>)}</select></label>
                <label><span>TO (UTC)</span><select value={filters.dateTo} onChange={(e) => setFilters({ ...filters, dateTo: e.target.value })}><option value="">{dates[dates.length - 1] ?? 'last'}</option>{dates.map((d) => <option key={d} value={d}>{d}</option>)}</select></label>
                <label><span>MIN CONFIDENCE</span><select value={filters.minConfidence} onChange={(e) => setFilters({ ...filters, minConfidence: e.target.value as Filters['minConfidence'] })}><option value="">Any</option><option value="nominal">Nominal +</option><option value="high">High</option></select></label>
                <label><span>DAY / NIGHT</span><select value={filters.daynight} onChange={(e) => setFilters({ ...filters, daynight: e.target.value as Filters['daynight'] })}><option value="">Both</option><option value="D">Day</option><option value="N">Night</option></select></label>
              </div>
              {filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo && <span className="input-error">“From” is after “To”.</span>}
            </div>
          )}

          {/* The map stays mounted (hidden) on other tabs: keeps the view and avoids Leaflet teardown mid-animation. */}
          <div hidden={tab !== 'map'}>
            <GlobalMap filters={filters} area={area} onArea={setArea} focus={focus} visible={tab === 'map'} />
            <AreaInput area={area} onArea={setArea} />
            {tab === 'map' && <AnalyticsPanel filters={filters} area={area} onFocus={onFocus} />}
          </div>
          {tab === 'compare' && <ComparePanel filters={filters} area={area} />}
          {tab === 'history' && <HistoricalPanel />}
          {tab === 'data' && <DataPanel datasets={datasets} error={catalogError} />}
          {tab === 'method' && <Methodology />}

          <footer><span>FIRESYNC · TEAM WE ARE ALIENS · NASA SPACE APPS CAIRO 2026</span><span>Data: NASA FIRMS (MODIS C6.1 NRT, VIIRS C2 NRT). Not an official NASA product.</span></footer>
        </div>
      </section>
    </main>
  )
}

export default App
