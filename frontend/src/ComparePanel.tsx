import { useEffect, useState } from 'react'
import { GroupedBars, LogScatter, StatTile } from './charts.tsx'
import { SOURCES, describeArea, filterParams, fmt, getJson, type Area, type CompareResponse, type Filters, type Side, type SourceId } from './api.ts'

type Config = { a: SourceId; b: SourceId; satA: string; window: number; cell: number }
const PRESETS: { label: string; hint: string; config: Config }[] = [
  { label: 'NOAA-20 ↔ NOAA-21', hint: 'Same VIIRS design, two satellites ~50 min apart', config: { a: 'noaa20', b: 'noaa21', satA: '', window: 60, cell: 0.1 } },
  { label: 'MODIS Aqua ↔ NOAA-21', hint: 'Different instruments, ~1–3 h apart — lagged', config: { a: 'modis', b: 'noaa21', satA: 'A', window: 180, cell: 0.1 } },
  { label: 'MODIS Aqua ↔ NOAA-20', hint: 'Different instruments, ~1–3 h apart — lagged', config: { a: 'modis', b: 'noaa20', satA: 'A', window: 180, cell: 0.1 } },
  { label: 'MODIS ↔ NOAA-21, strict ±60 min', hint: 'Only the few truly close overpasses', config: { a: 'modis', b: 'noaa21', satA: '', window: 60, cell: 0.1 } },
]

function relGain(cal: CompareResponse['calibration']) {
  const fit = cal.validation_fitted?.median_abs_error_log10, base = cal.validation_identity_baseline?.median_abs_error_log10
  return fit !== undefined && base ? (base - fit) / base : 0
}

function slopeVerdict(slope: number, ci?: [number, number]) {
  if (!ci || !Number.isFinite(slope)) return ''
  if (ci[0] <= 1 && ci[1] >= 1) return 'The slope is statistically consistent with 1: the sensors scale alike, differing by a constant factor.'
  if (Math.abs(slope - 1) < 0.05) return `The slope (${slope.toFixed(3)}) is within 5% of 1. Its narrow interval excludes 1, but those intervals understate uncertainty, so in practice the relationship is close to a constant factor.`
  return `The slope (${slope.toFixed(3)}) differs materially from 1: the sensors do not scale alike across the FRP range.`
}

function gainVerdict(fit?: number, base?: number) {
  if (fit === undefined || base === undefined) return ''
  if (fit >= base) return 'On held-out dates the fit does NOT reduce error versus 1:1, so applying it is not justified.'
  const gain = (base - fit) / base
  return gain < 0.05
    ? `On held-out dates it reduces median error by only ${(gain * 100).toFixed(0)}%. Scatter from fire variability between looks dominates, and the 1:1 assumption is nearly as good.`
    : `On held-out dates it reduces median error by ${(gain * 100).toFixed(0)}% versus 1:1.`
}

function SideBar({ side, label, other, color }: { side: Side; label: string; other: string; color: string }) {
  const total = side.cell_overpasses || 1
  const footprint = side.coverage_method === 'swath_footprint'
  const seg = [
    { key: 'co', value: side.co_detected, cls: 'seg-co', text: `co-detected by ${other}` },
    { key: 'viewed', value: side.other_viewed_area_no_detection, cls: 'seg-viewed', text: footprint ? `in ${other}'s swath, no detection` : `${other} detected nearby, none in cell (proxy)` },
    { key: 'notview', value: side.other_not_in_view, cls: 'seg-notview', text: `not in ${other}'s swath` },
    { key: 'unknown', value: side.other_coverage_unknown, cls: 'seg-unknown', text: footprint ? `${other} catalogue gap (unknown)` : `${other} coverage unknown` },
    { key: 'elsewhere', value: side.other_look_paired_elsewhere, cls: 'seg-elsewhere', text: `${other} look already paired with another ${label} look` },
  ].filter((s) => s.key !== 'notview' || footprint)
  return (
    <div className="side-bar">
      <div className="side-bar-head">
        <span><i className="swatch" style={{ background: color }} />{label}: {fmt(side.cell_overpasses)} cell-overpasses <span className={`method-tag ${footprint ? 'fp' : 'proxy'}`}>{footprint ? `${other} swath footprints` : 'coverage proxy'}</span></span>
        <b>{side.conditional_co_detection_rate === null ? '—' : `${fmt(side.conditional_co_detection_rate * 100, 1)}%`} <small>co-detection where the fire was {footprint ? `in ${other}'s swath` : `near ${other} detections`}</small></b>
      </div>
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
            <StatTile label={`MEDIAN FRP RATIO ${nameB} / ${nameA}`} value={fmt(r.frp.median_ratio_b_over_a, 2)} foot={r.frp.median_ratio_95ci_day_clustered ? `95% CI ${fmt(r.frp.median_ratio_95ci_day_clustered[0], 2)}–${fmt(r.frp.median_ratio_95ci_day_clustered[1], 2)} (resampling whole days, ${r.frp.days} days)` : r.frp.median_ratio_95ci ? `95% CI ${fmt(r.frp.median_ratio_95ci[0], 2)}–${fmt(r.frp.median_ratio_95ci[1], 2)} (pair bootstrap; too few days to resample by day)` : ''} />
            <StatTile label="SPATIAL OVERLAP (JACCARD)" value={fmt(r.spatial_occupancy.jaccard, 2)} foot={`${fmt(r.spatial_occupancy.cells_both)} of ${fmt(r.spatial_occupancy.cells_a + r.spatial_occupancy.cells_b - r.spatial_occupancy.cells_both)} active cells shared, any time`} />
          </div>

          <div className="chart-card wide">
            <h3>Detection agreement <small>separating “not detected” from “not observed”</small></h3>
            <SideBar side={r.a_side} label={nameA} other={nameB} color={SOURCES[r.source_a].color} />
            <SideBar side={r.b_side} label={nameB} other={nameA} color={SOURCES[r.source_b].color} />
            <p className="chart-note">Rate = co-detected ÷ (co-detected + in the other satellite's swath with no detection). <b>Swath footprints</b> are NASA CMR granule outlines (the FIRMS "Satellite Swath Outlines"); every catalogued detection of each satellite falls inside its own footprints. They show what was in view, not whether it was cloud-free, so some "no detection" cases are clouds. Catalogue gaps are "unknown", and neither "unknown" nor "already paired" counts toward the rate. Where a satellite has no footprints, a weaker proxy is used: the other satellite detected something in the same 1° cell within the window.</p>
          </div>

          <div className="chart-grid two">
            <div className="chart-card">
              <h3>Cell-overpass FRP, paired <small>log–log · Σ pixel FRP per cell-overpass (MW) · sample of {fmt(r.frp.scatter_sample_log10?.length ?? 0)}</small></h3>
              <LogScatter points={r.frp.scatter_sample_log10 ?? []} xLabel={`${nameA} FRP (MW)`} yLabel={`${nameB} FRP (MW)`} color={SOURCES[r.source_b].color} fit={cal.status === 'fitted' && cal.slope != null && cal.intercept != null ? { slope: cal.slope, intercept: cal.intercept } : null} />
            </div>
            <div className="chart-card">
              <h3>Time offset of paired looks <small>t({nameB}) − t({nameA}), minutes</small></h3>
              <GroupedBars categories={r.timing.dt_histogram.edges.slice(0, -1).map(String)} formatCategory={(c) => `${Number(c) > 0 ? '+' : ''}${c}`} valueLabel="Time offset histogram" series={[{ key: 'dt', label: 'pairs', color: SOURCES[r.source_b].color, values: r.timing.dt_histogram.counts }]} height={190} width={400} />
              {r.frp.order_test?.b_later && r.frp.order_test?.b_earlier && (() => {
                const later = r.frp.order_test.b_later!, earlier = r.frp.order_test.b_earlier!
                const overlap = later.ci95[0] <= earlier.ci95[1] && earlier.ci95[0] <= later.ci95[1]
                return (
                  <div className="calibration-card order">
                    <h4>Overpass-order test · {overlap ? 'ratio is order-independent' : 'ratio depends on order'}</h4>
                    <table className="data-table compact">
                      <thead><tr><th>Which looked first?</th><th>Pairs</th><th>Median {nameB}/{nameA}</th><th>95% CI</th></tr></thead>
                      <tbody>
                        <tr><td>{nameA} first</td><td>{fmt(later.pairs)}</td><td>{fmt(later.median_ratio_b_over_a, 3)}</td><td>{fmt(later.ci95[0], 3)}–{fmt(later.ci95[1], 3)}</td></tr>
                        <tr><td>{nameB} first</td><td>{fmt(earlier.pairs)}</td><td>{fmt(earlier.median_ratio_b_over_a, 3)}</td><td>{fmt(earlier.ci95[0], 3)}–{fmt(earlier.ci95[1], 3)}</td></tr>
                      </tbody>
                    </table>
                    <p>{overlap ? `If the FRP difference were caused by fires growing or fading between looks, it would reverse when the order reverses. It does not, which points to a systematic difference between the two observations (sensor calibration, viewing geometry or processing) rather than fire evolution. These intervals resample pairs and are narrower than day-level uncertainty.` : 'The ratio changes with overpass order, so part of the difference reflects fire evolution between looks, not only the sensors.'}</p>
                  </div>
                )
              })()}
              <div className={`calibration-card ${cal.status}`}>
                <h4>Cross-sensor FRP calibration · {cal.status === 'fitted' ? (!cal.improves_on_identity ? 'fitted, does NOT beat 1:1' : relGain(cal) < 0.05 ? 'fitted, marginal gain over 1:1' : 'fitted, improves on 1:1') : cal.status.replace('_', ' ')}</h4>
                {cal.status === 'fitted' ? (
                  <>
                    <p className="mono">log₁₀ FRP<sub>{nameB}</sub> = {fmt(cal.intercept, 3)} + {fmt(cal.slope, 3)} · log₁₀ FRP<sub>{nameA}</sub></p>
                    <p className="verdict">{slopeVerdict(cal.slope ?? NaN, cal.slope_95ci)} {gainVerdict(cal.validation_fitted?.median_abs_error_log10, cal.validation_identity_baseline?.median_abs_error_log10)}</p>
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
