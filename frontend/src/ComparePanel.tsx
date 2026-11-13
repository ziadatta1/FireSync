import { useEffect, useState } from 'react'
import { GroupedBars, LogScatter, StatTile } from './charts.tsx'
import { SOURCES, describeArea, filterParams, fmt, getJson, type Area, type CompareResponse, type Filters, type Side, type SourceId } from './api.ts'

type Config = { a: SourceId; b: SourceId; satA: string; window: number; cell: number }
const PRESETS: { label: string; hint: string; config: Config }[] = [
  { label: 'NOAA-20 ↔ NOAA-21', hint: 'Same instrument, ~50 min apart — near-simultaneous', config: { a: 'noaa20', b: 'noaa21', satA: '', window: 60, cell: 0.1 } },
  { label: 'MODIS Aqua ↔ NOAA-21', hint: 'Different instruments, ~1–3 h apart — lagged', config: { a: 'modis', b: 'noaa21', satA: 'A', window: 180, cell: 0.1 } },
  { label: 'MODIS Aqua ↔ NOAA-20', hint: 'Different instruments, ~1–3 h apart — lagged', config: { a: 'modis', b: 'noaa20', satA: 'A', window: 180, cell: 0.1 } },
  { label: 'MODIS ↔ NOAA-21, strict ±60 min', hint: 'Only the few truly close overpasses', config: { a: 'modis', b: 'noaa21', satA: '', window: 60, cell: 0.1 } },
]

function SideBar({ side, label, other, color }: { side: Side; label: string; other: string; color: string }) {
  const total = side.cell_overpasses || 1
  const seg = [
    { key: 'co', value: side.co_detected, cls: 'seg-co', text: `co-detected by ${other}` },
    { key: 'viewed', value: side.other_viewed_area_no_detection, cls: 'seg-viewed', text: `${other} viewed area, no detection in cell (proxy)` },
    { key: 'unknown', value: side.other_coverage_unknown, cls: 'seg-unknown', text: `${other} coverage unknown` },
  ]
  return (
    <div className="side-bar">
      <div className="side-bar-head"><span><i className="swatch" style={{ background: color }} />{label}: {fmt(side.cell_overpasses)} cell-overpasses</span><b>{side.conditional_co_detection_rate === null ? '—' : `${fmt(side.conditional_co_detection_rate * 100, 1)}%`} <small>co-detection where {other} viewed</small></b></div>
      <div className="stack" role="img" aria-label={`${label} cell-overpass outcomes`}>
        {seg.map((s) => s.value > 0 && <span key={s.key} className={s.cls} style={{ width: `${(s.value / total) * 100}%` }} title={`${s.text}: ${fmt(s.value)}`} />)}
      </div>
      <div className="stack-legend">{seg.map((s) => <span key={s.key}><i className={s.cls} />{s.text}: <b>{fmt(s.value)}</b> ({fmt((s.value / total) * 100, 1)}%)</span>)}</div>
    </div>
  )
}

export default function ComparePanel({ filters, area }: { filters: Filters; area: Area | null }) {
  const [config, setConfig] = useState<Config>(PRESETS[0].config)
  const [useArea, setUseArea] = useState(true)
  const [result, setResult] = useState<CompareResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    const params = filterParams(filters, useArea ? area : null, false)
    params.set('a', config.a); params.set('b', config.b)
    params.set('window', String(config.window)); params.set('cell', String(config.cell))
    if (config.satA) params.set('satellite_a', config.satA)
    setLoading(true); setError('')
    getJson<CompareResponse>(`/api/global/compare?${params}`, controller.signal)
      .then(setResult)
      .catch((e: unknown) => { if (!(e instanceof DOMException)) setError(e instanceof Error ? e.message : 'Comparison unavailable') })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [config, filters, area, useArea])

  const r = result
  const cal = r?.calibration
  const nameA = r ? `${SOURCES[r.source_a].short}${r.satellite_filter.a === 'A' ? ' Aqua' : r.satellite_filter.a === 'T' ? ' Terra' : ''}` : ''
  const nameB = r ? SOURCES[r.source_b].short : ''

  return (
    <section className="compare" aria-labelledby="compare-title">
      <div className="section-heading">
        <div><span className="section-kicker">SENSOR COMPARISON</span><h2 id="compare-title">How do the satellites agree?</h2></div>
        <span className="catalog-meta">{describeArea(useArea ? area : null)}</span>
      </div>
      <p className="lede small">Paired comparison on a common grid. Each sensor's detections are grouped into <em>cell-overpasses</em> (one satellite look at one {config.cell}° cell), then matched one-to-one to the other sensor's nearest look at the same cell within ±{config.window} min. No values are averaged or merged across sensors.</p>

      <div className="compare-controls">
        <div className="preset-row">
          {PRESETS.map((p) => (
            <button key={p.label} type="button" className={`preset ${JSON.stringify(p.config) === JSON.stringify(config) ? 'active' : ''}`} onClick={() => setConfig(p.config)}>
              <strong>{p.label}</strong><span>{p.hint}</span>
            </button>
          ))}
        </div>
        <div className="filter-fields compact">
          <label><span>SENSOR A</span><select value={config.a} onChange={(e) => setConfig({ ...config, a: e.target.value as SourceId, satA: e.target.value === 'modis' ? config.satA : '' })}>{(['modis', 'noaa20', 'noaa21'] as SourceId[]).map((s) => <option key={s} value={s}>{SOURCES[s].label}</option>)}</select></label>
          {config.a === 'modis' && <label><span>MODIS PLATFORM</span><select value={config.satA} onChange={(e) => setConfig({ ...config, satA: e.target.value })}><option value="">Terra + Aqua</option><option value="A">Aqua only</option><option value="T">Terra only</option></select></label>}
          <label><span>SENSOR B</span><select value={config.b} onChange={(e) => setConfig({ ...config, b: e.target.value as SourceId })}>{(['modis', 'noaa20', 'noaa21'] as SourceId[]).filter((s) => s !== config.a).map((s) => <option key={s} value={s}>{SOURCES[s].label}</option>)}</select></label>
          <label><span>TIME WINDOW</span><select value={config.window} onChange={(e) => setConfig({ ...config, window: Number(e.target.value) })}>{[30, 60, 90, 120, 180, 240].map((w) => <option key={w} value={w}>±{w} min</option>)}</select></label>
          <label><span>GRID</span><select value={config.cell} onChange={(e) => setConfig({ ...config, cell: Number(e.target.value) })}>{[0.05, 0.1, 0.25, 0.5].map((c) => <option key={c} value={c}>{c}°</option>)}</select></label>
          <label className="checkbox"><input type="checkbox" checked={useArea} disabled={!area} onChange={(e) => setUseArea(e.target.checked)} /><span>{area ? 'Restrict to selected area' : 'Draw an area on the map to restrict'}</span></label>
        </div>
      </div>

      {error && <div className="error-panel">Comparison unavailable: {error}</div>}
      {loading && !r && <div className="loading-card">Pairing overpasses<span className="loading-pulse" /></div>}
      {r && cal && (
        <div className={loading ? 'is-refreshing' : ''}>
          <div className="stat-row">
            <StatTile label="PAIRED CELL-OVERPASSES" value={fmt(r.timing.pairs)} foot={`dates ${r.compared_dates[0]} → ${r.compared_dates[1]} (shared by both)`} />
            <StatTile label="MEDIAN |Δt| BETWEEN PAIRED LOOKS" value={r.timing.median_abs_dt_min === null ? '—' : `${fmt(r.timing.median_abs_dt_min)} min`} foot={r.timing.median_abs_dt_min !== null && r.timing.median_abs_dt_min > 60 ? 'Not simultaneous — fires evolve in this time' : 'Near-simultaneous'} />
            <StatTile label="log-FRP CORRELATION (PEARSON / SPEARMAN)" value={`${fmt(r.frp.pearson_r_log10, 2)} / ${fmt(r.frp.spearman_rho, 2)}`} foot={`${fmt(r.frp.pairs)} pairs with FRP > 0 on both`} />
            <StatTile label={`MEDIAN FRP RATIO ${nameB} / ${nameA}`} value={fmt(r.frp.median_ratio_b_over_a, 2)} foot={r.frp.median_ratio_95ci ? `95% CI ${fmt(r.frp.median_ratio_95ci[0], 2)}–${fmt(r.frp.median_ratio_95ci[1], 2)} (bootstrap)` : ''} />
            <StatTile label="SPATIAL OVERLAP (JACCARD)" value={fmt(r.spatial_occupancy.jaccard, 2)} foot={`${fmt(r.spatial_occupancy.cells_both)} of ${fmt(r.spatial_occupancy.cells_a + r.spatial_occupancy.cells_b - r.spatial_occupancy.cells_both)} active cells shared, any time`} />
          </div>

          <div className="chart-card wide">
            <h3>Detection agreement <small>separating “not detected” from “not observed”</small></h3>
            <SideBar side={r.a_side} label={nameA} other={nameB} color={SOURCES[r.source_a].color} />
            <SideBar side={r.b_side} label={nameB} other={nameA} color={SOURCES[r.source_b].color} />
            <p className="chart-note">“Viewed area” is a coverage proxy: the other sensor produced a detection somewhere in the same 1° cell within the window, so that part of the scene was imaged. FIRMS files contain detections only — no swath or cloud masks — so a proxy “non-detection” may still be cloud-obscured. “Coverage unknown” is never counted as disagreement.</p>
          </div>

          <div className="chart-grid two">
            <div className="chart-card">
              <h3>Cell-overpass FRP, paired <small>log–log · Σ pixel FRP per cell-overpass (MW) · sample of {fmt(r.frp.scatter_sample_log10?.length ?? 0)}</small></h3>
              <LogScatter points={r.frp.scatter_sample_log10 ?? []} xLabel={`${nameA} FRP (MW)`} yLabel={`${nameB} FRP (MW)`} color={SOURCES[r.source_b].color} fit={cal.status === 'fitted' && cal.slope != null && cal.intercept != null ? { slope: cal.slope, intercept: cal.intercept } : null} />
            </div>
            <div className="chart-card">
              <h3>Time offset of paired looks <small>t({nameB}) − t({nameA}), minutes</small></h3>
              <GroupedBars categories={r.timing.dt_histogram.edges.slice(0, -1).map(String)} formatCategory={(c) => `${Number(c) > 0 ? '+' : ''}${c}`} valueLabel="Time offset histogram" series={[{ key: 'dt', label: 'pairs', color: SOURCES[r.source_b].color, values: r.timing.dt_histogram.counts }]} height={180} />
              <div className={`calibration-card ${cal.status}`}>
                <h4>Cross-sensor FRP calibration · {cal.status === 'fitted' ? (cal.improves_on_identity ? 'fitted, validated' : 'fitted, does NOT beat 1:1') : cal.status.replace('_', ' ')}</h4>
                {cal.status === 'fitted' ? (
                  <>
                    <p className="mono">log₁₀ FRP<sub>{nameB}</sub> = {fmt(cal.intercept, 3)} + {fmt(cal.slope, 3)} · log₁₀ FRP<sub>{nameA}</sub></p>
                    <p className="verdict">{cal.slope_95ci && cal.slope_95ci[0] <= 1 && cal.slope_95ci[1] >= 1 ? 'Slope is statistically consistent with 1 — the sensors scale alike; only a constant offset is suggested.' : 'Slope differs from 1 — the relationship is not a simple constant offset.'} {cal.improves_on_identity ? 'The fit reduces held-out error versus 1:1.' : 'The fit does NOT reduce held-out error versus 1:1, so applying it is not justified.'}</p>
                    <p>Slope 95% CI {fmt(cal.slope_95ci?.[0], 3)}–{fmt(cal.slope_95ci?.[1], 3)}. Reduced-major-axis fit on {fmt(cal.train_pairs)} pairs from {cal.train_dates?.[0]}…{cal.train_dates?.[cal.train_dates.length - 1]}; validated on {fmt(cal.validation_pairs)} held-out pairs from {cal.validation_dates?.join(', ')}.</p>
                    <table className="data-table compact">
                      <thead><tr><th>Held-out error (log₁₀)</th><th>Fitted</th><th>1:1 baseline</th></tr></thead>
                      <tbody>
                        <tr><td>Median |error|</td><td>{fmt(cal.validation_fitted?.median_abs_error_log10, 3)} (×{fmt(cal.validation_fitted?.median_abs_error_factor, 2)})</td><td>{fmt(cal.validation_identity_baseline?.median_abs_error_log10, 3)} (×{fmt(cal.validation_identity_baseline?.median_abs_error_factor, 2)})</td></tr>
                        <tr><td>Bias</td><td>{fmt(cal.validation_fitted?.bias_log10, 3)}</td><td>{fmt(cal.validation_identity_baseline?.bias_log10, 3)}</td></tr>
                        <tr><td>RMSE</td><td>{fmt(cal.validation_fitted?.rmse_log10, 3)}</td><td>{fmt(cal.validation_identity_baseline?.rmse_log10, 3)}</td></tr>
                      </tbody>
                    </table>
                    <ul>{cal.caveats?.map((c) => <li key={c}>{c}</li>)}</ul>
                  </>
                ) : <p>{cal.reason}</p>}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
