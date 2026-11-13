import { useEffect, useState } from 'react'
import { GroupedBars, StatTile, type Series } from './charts.tsx'
import { API, SOURCES, SOURCE_IDS, describeArea, filterParams, fmt, getJson, type Area, type Filters, type SourceId, type StatsResponse } from './api.ts'

const shortDate = (d: string) => d.slice(5)

export default function AnalyticsPanel({ filters, area, onFocus }: {
  filters: Filters
  area: Area | null
  onFocus: (lat: number, lon: number) => void
}) {
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!filters.sources.length) { setStats(null); return }
    const controller = new AbortController()
    setLoading(true); setError('')
    getJson<StatsResponse>(`/api/global/stats?${filterParams(filters, area)}`, controller.signal)
      .then(setStats)
      .catch((e: unknown) => { if (!(e instanceof DOMException)) setError(e instanceof Error ? e.message : 'Statistics unavailable') })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [filters, area])

  const active = SOURCE_IDS.filter((s) => stats?.sources[s])
  const total = active.reduce((sum, s) => sum + (stats?.sources[s]?.detections ?? 0), 0)
  const series = (pick: (s: SourceId) => (number | null)[]): Series[] =>
    active.map((s) => ({ key: s, label: SOURCES[s].short, color: SOURCES[s].color, values: pick(s) }))
  const exportUrl = `${API}/api/global/export.csv?${filterParams(filters, area)}`

  return (
    <section className="analytics" aria-labelledby="analytics-title">
      <div className="section-heading">
        <div><span className="section-kicker">AREA ANALYSIS</span><h2 id="analytics-title">{area ? 'Selected area' : 'Global summary'}</h2></div>
        <span className="catalog-meta">{describeArea(area)}</span>
      </div>
      {error && <div className="error-panel">Statistics unavailable: {error}</div>}
      {!filters.sources.length && <div className="loading-card">Enable a source to compute statistics.</div>}
      {loading && !stats && <div className="loading-card">Computing statistics<span className="loading-pulse" /></div>}
      {stats && (
        <div className={loading ? 'is-refreshing' : ''}>
          <div className="stat-row">
            <StatTile label="DETECTIONS (ALL SOURCES)" value={fmt(total)} foot="Fire-pixel detections · not unique fires; sensors overlap" />
            {active.map((s) => {
              const st = stats.sources[s]!
              return <StatTile key={s} accent={SOURCES[s].color} label={SOURCES[s].label.toUpperCase()} value={fmt(st.detections)} foot={st.frp ? `median FRP ${fmt(st.frp.median, 1)} MW · p90 ${fmt(st.frp.p90, 1)}` : 'no detections'} />
            })}
          </div>
          {total === 0 && <div className="map-warning">No detections in this selection. This means no fire pixel was detected — it does not establish the absence of fire (clouds, overpass timing, detection thresholds).</div>}

          <div className="chart-grid">
            <div className="chart-card wide">
              <h3>Daily detections <small>UTC acquisition date · “·” = date outside that dataset</small></h3>
              <GroupedBars
                categories={stats.dates}
                formatCategory={shortDate}
                valueLabel="Daily detections"
                series={series((s) => stats.dates.map((d) => (d in stats.sources[s]!.daily_detections ? stats.sources[s]!.daily_detections[d] : null)))}
                note={stats.dates.length ? `The last date (${stats.dates[stats.dates.length - 1]}) is a partial day in the downloaded snapshot.` : undefined}
              />
            </div>
            <div className="chart-card">
              <h3>Detections by hour <small>UTC · reflects overpass schedule, not fire timing</small></h3>
              <GroupedBars categories={Array.from({ length: 24 }, (_, h) => String(h).padStart(2, '0'))} valueLabel="Detections by UTC hour" series={series((s) => stats.sources[s]!.hourly_utc)} height={170} />
            </div>
            <div className="chart-card">
              <h3>FRP distribution <small>detections per FRP bin (MW, lower edge)</small></h3>
              <GroupedBars
                categories={(stats.sources[active[0]]?.frp_histogram.edges ?? []).map(String)}
                formatCategory={(c) => `≥${c}`}
                valueLabel="FRP histogram"
                series={series((s) => stats.sources[s]!.frp_histogram.counts)}
                height={170}
                note="Pixel FRP is not comparable 1:1 across sensors: MODIS pixels are ~7× larger in area than VIIRS 375 m pixels."
              />
            </div>
            <div className="chart-card">
              <h3>Confidence class <small>FIRMS classes; MODIS 0–100 mapped &lt;30 / 30–79 / ≥80</small></h3>
              <GroupedBars categories={['low', 'nominal', 'high']} valueLabel="Confidence classes" series={series((s) => { const c = stats.sources[s]!.confidence; return [c.low, c.nominal, c.high] })} height={170} />
            </div>
            <div className="chart-card">
              <h3>FRP statistics <small>MW per detection</small></h3>
              <table className="data-table">
                <thead><tr><th>Source</th><th>n</th><th>median</th><th>p75</th><th>p99</th><th>max</th><th>Σ FRP</th></tr></thead>
                <tbody>
                  {active.map((s) => { const f = stats.sources[s]!.frp; return (
                    <tr key={s}><td><i className="swatch" style={{ background: SOURCES[s].color }} />{SOURCES[s].short}</td><td>{fmt(f?.n)}</td><td>{fmt(f?.median, 1)}</td><td>{fmt(f?.p75, 1)}</td><td>{fmt(f?.p99, 1)}</td><td>{fmt(f?.max, 0)}</td><td>{fmt(f?.sum, 0)}</td></tr>
                  ) })}
                </tbody>
              </table>
              <table className="data-table compact">
                <thead><tr><th>Source</th><th>Day</th><th>Night</th><th>Satellites</th></tr></thead>
                <tbody>
                  {active.map((s) => { const st = stats.sources[s]!; return (
                    <tr key={s}><td>{SOURCES[s].short}</td><td>{fmt(st.daynight.day)}</td><td>{fmt(st.daynight.night)}</td><td>{Object.entries(st.satellites).map(([k, v]) => `${k} ${fmt(v)}`).join(' · ')}</td></tr>
                  ) })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="chart-card wide">
            <h3>Most active {stats.sources[active[0]]?.top_cell_degrees ?? 0.25}° cells <small>by detection count, per source · click to fly there</small></h3>
            <div className="top-cells">
              {active.map((s) => (
                <div key={s}>
                  <div className="top-cells-head"><i className="swatch" style={{ background: SOURCES[s].color }} />{SOURCES[s].short}</div>
                  <table className="data-table compact">
                    <thead><tr><th>#</th><th>Cell centre</th><th>Det.</th><th>Σ FRP MW</th></tr></thead>
                    <tbody>
                      {stats.sources[s]!.top_cells.map((c, i) => (
                        <tr key={i} className="clickable" onClick={() => onFocus(c.lat_center, c.lon_center)}>
                          <td>{i + 1}</td><td>{c.lat_center.toFixed(2)}, {c.lon_center.toFixed(2)}</td><td>{fmt(c.detections)}</td><td>{fmt(c.frp_sum_mw)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </div>
          <div className="export-row">
            <a className="apply-filters" href={exportUrl} download>Export {fmt(total)} source rows (CSV)</a>
            <span>Rows are exported unchanged from the FIRMS files, with dataset, FIRMS source, file SHA-256 and source row number added for traceability.</span>
          </div>
        </div>
      )}
    </section>
  )
}
