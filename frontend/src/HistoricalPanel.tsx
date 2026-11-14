import { useEffect, useMemo, useState } from 'react'
import { StatTile } from './charts.tsx'
import { fmt, getJson } from './api.ts'

type Region = { id: string; label: string; bbox: number[]; regime: string; available: boolean; coverage: Record<string, { ok: number; failed: number; days: number; first: string | null; last: string | null }> }
type MonthRow = { month: string; complete: boolean; split: 'train' | 'test'; viirs: number | null; modis: number | null; modis_aqua: number | null; modis_terra: number | null; modis_harmonized: number | null }
type Calendar = { region: string; label: string; months: MonthRow[]; harmonization_model: string | null }
type Metrics = { months: number; median_abs_log10_error: number; median_abs_error_factor: number; bias_log10: number; spearman_monthly: number | null; annual_total_median_abs_pct_error: number }
type ModelResult = { label: string; test: Metrics; train_in_sample: Metrics; test_by_year: Record<string, Metrics>; test_by_region: Record<string, Metrics>; anomaly_agreement_test: { months: number; anomaly_correlation: number | null; sign_agreement: number | null; sign_agreement_large_anomalies: number | null; large_anomaly_months: number } }
type Validation = {
  status: string
  design?: { train_years: number[]; test_years: number[]; chosen_lambda: number; cv_median_abs_log10_error_by_lambda: Record<string, number> }
  regions?: string[]
  months?: { train: number; test: number; excluded_incomplete: number }
  parameters?: { pooled_ratio: number; region: Record<string, { ratio: number; a: number; b: number }> }
  models?: Record<string, ModelResult>
  selected_model?: string
  selection?: { method: string; cv_median_abs_log10_error_by_model: Record<string, number>; no_harmonization_beats_selected_in_training_cv: boolean }
  selected_beats_no_harmonization_on_test?: boolean
  selected_beats_pooled_ratio_on_test?: boolean | null
  lowest_test_error_model_diagnostic_only?: string
  reason?: string
  ratio_viirs_over_modis_by_year?: { region: string; year: number; ratio: number }[]
  train_months?: number
  test_months?: number
}
type Series = 'viirs' | 'modis_harmonized' | 'modis' | 'ratio'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const SERIES: { id: Series; label: string }[] = [
  { id: 'viirs', label: 'VIIRS S-NPP (observed)' },
  { id: 'modis_harmonized', label: 'MODIS → VIIRS-equivalent' },
  { id: 'modis', label: 'MODIS (observed, raw)' },
  { id: 'ratio', label: 'Harmonized ÷ VIIRS' },
]
// Single-hue sequential ramp (dark surface → bright fire orange), log-scaled.
function rampColor(t: number) {
  const a = [34, 30, 26], b = [255, 168, 84]
  const k = Math.max(0, Math.min(1, t))
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(',')})`
}
// Diverging ramp for ratios: blue (under) ← grey (1.0) → orange (over).
function divergingColor(ratio: number) {
  const x = Math.max(-1, Math.min(1, Math.log2(ratio)))
  const mid = [96, 106, 101], lo = [57, 135, 229], hi = [217, 89, 38]
  const end = x < 0 ? lo : hi
  const k = Math.abs(x)
  return `rgb(${mid.map((v, i) => Math.round(v + (end[i] - v) * k)).join(',')})`
}

function value(row: MonthRow, series: Series): number | null {
  if (!row.complete) return null
  if (series === 'ratio') return row.viirs && row.modis_harmonized !== null ? row.modis_harmonized / Math.max(row.viirs, 1) : null
  return row[series]
}

function Heatmap({ rows, series, testFrom }: { rows: MonthRow[]; series: Series; testFrom: number }) {
  const [hover, setHover] = useState<MonthRow | null>(null)
  const years = [...new Set(rows.map((r) => Number(r.month.slice(0, 4))))]
  const byKey = new Map(rows.map((r) => [r.month, r]))
  const max = Math.max(1, ...rows.map((r) => (series === 'ratio' ? 0 : value(r, series) ?? 0)))
  const color = (r: MonthRow | undefined) => {
    if (!r) return 'transparent'
    const v = value(r, series)
    if (v === null) return 'url(#hatch)'
    if (series === 'ratio') return divergingColor(v)
    return rampColor(Math.log10(v + 1) / Math.log10(max + 1))
  }
  const cw = 46, ch = 22, left = 46, top = 20
  return (
    <div className="chart heatmap" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${left + cw * 12 + 70} ${top + ch * years.length + 6}`} role="img" aria-label="Burning activity calendar">
        <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#1a2420" /><line x1="0" y1="0" x2="0" y2="6" stroke="#3a4842" strokeWidth="2" /></pattern></defs>
        {MONTHS.map((m, i) => <text key={m} x={left + i * cw + cw / 2} y={13} className="tick" textAnchor="middle">{m}</text>)}
        {years.map((y, yi) => (
          <g key={y}>
            <text x={left - 8} y={top + yi * ch + ch / 2 + 4} className="tick" textAnchor="end">{y}</text>
            {MONTHS.map((_, mi) => {
              const key = `${y}-${String(mi + 1).padStart(2, '0')}`
              const r = byKey.get(key)
              return <rect key={key} x={left + mi * cw + 1} y={top + yi * ch + 1} width={cw - 2} height={ch - 2} rx={3} fill={color(r)} onMouseEnter={() => r && setHover(r)} className={hover?.month === key ? 'cell-hover' : ''} />
            })}
            {y === testFrom && <g><line x1={left - 40} x2={left + cw * 12 + 4} y1={top + yi * ch} y2={top + yi * ch} className="split-line" /><text x={left + cw * 12 + 8} y={top + yi * ch + 12} className="split-label">TEST ↓</text></g>}
            {yi === 0 && <text x={left + cw * 12 + 8} y={top + 12} className="split-label train">TRAIN ↓</text>}
          </g>
        ))}
      </svg>
      {hover && (
        <div className="chart-tooltip static">
          <strong>{hover.month} · {hover.split === 'test' ? 'test year' : 'training year'}</strong>
          {!hover.complete ? <div>Incomplete archive month — excluded (not zero)</div> : (
            <>
              <div>VIIRS S-NPP<b>{fmt(hover.viirs)}</b></div>
              <div>MODIS raw (Aqua {fmt(hover.modis_aqua)} · Terra {fmt(hover.modis_terra)})<b>{fmt(hover.modis)}</b></div>
              <div>MODIS → VIIRS-equivalent<b>{fmt(hover.modis_harmonized)}</b></div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function TimeSeries({ rows, testFrom }: { rows: MonthRow[]; testFrom: number }) {
  const [hover, setHover] = useState<number | null>(null)
  const w = 900, h = 220, pad = { l: 52, r: 10, t: 12, b: 24 }
  const vals = rows.flatMap((r) => [r.viirs ?? 0, r.modis_harmonized ?? 0])
  const max = Math.max(10, ...vals)
  const lmax = Math.log10(max + 1)
  const x = (i: number) => pad.l + (i / Math.max(rows.length - 1, 1)) * (w - pad.l - pad.r)
  const y = (v: number) => pad.t + (1 - Math.log10(v + 1) / lmax) * (h - pad.t - pad.b)
  const path = (key: 'viirs' | 'modis_harmonized') => {
    let d = ''
    rows.forEach((r, i) => {
      const v = r.complete ? r[key] : null
      if (v === null) { d += ' '; return }
      d += `${d.endsWith(' ') || d === '' ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    })
    return d.replace(/ /g, '')
  }
  const testIndex = rows.findIndex((r) => Number(r.month.slice(0, 4)) >= testFrom)
  const ticks = [1, 10, 100, 1000, 10000, 100000].filter((t) => t <= max * 1.2)
  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <div className="chart-legend"><span><i style={{ background: '#3987e5' }} />VIIRS S-NPP observed</span><span><i style={{ background: '#d95926' }} />MODIS → VIIRS-equivalent</span><span><i className="test-key" />Test years</span></div>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Monthly detections, observed VIIRS vs harmonized MODIS" onMouseMove={(e) => {
        const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
        const px = ((e.clientX - box.left) / box.width) * w
        setHover(Math.max(0, Math.min(rows.length - 1, Math.round(((px - pad.l) / (w - pad.l - pad.r)) * (rows.length - 1)))))
      }}>
        {testIndex >= 0 && <rect x={x(testIndex)} y={pad.t} width={w - pad.r - x(testIndex)} height={h - pad.t - pad.b} className="test-band" />}
        {ticks.map((t) => <g key={t}><line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} className="grid" /><text x={pad.l - 6} y={y(t) + 3} className="tick" textAnchor="end">{fmt(t)}</text></g>)}
        {rows.filter((r) => r.month.endsWith('-01')).map((r) => { const i = rows.indexOf(r); return <text key={r.month} x={x(i)} y={h - 6} className="tick" textAnchor="middle">{r.month.slice(0, 4)}</text> })}
        <path d={path('viirs')} className="line" stroke="#3987e5" />
        <path d={path('modis_harmonized')} className="line" stroke="#d95926" />
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={h - pad.b} className="crosshair" />}
      </svg>
      {hover !== null && rows[hover] && <div className="chart-tooltip static"><strong>{rows[hover].month}</strong>{rows[hover].complete ? <><div><i style={{ background: '#3987e5' }} />VIIRS<b>{fmt(rows[hover].viirs)}</b></div><div><i style={{ background: '#d95926' }} />MODIS → VIIRS-eq.<b>{fmt(rows[hover].modis_harmonized)}</b></div></> : <div>Incomplete month (excluded)</div>}</div>}
      <p className="chart-note">Log scale. Gaps = incomplete archive months. The harmonization is fitted on 2012–2019 only; the shaded years were never used for fitting or tuning.</p>
    </div>
  )
}

export default function HistoricalPanel() {
  const [regions, setRegions] = useState<Region[]>([])
  const [region, setRegion] = useState('')
  const [cal, setCal] = useState<Calendar | null>(null)
  const [val, setVal] = useState<Validation | null>(null)
  const [series, setSeries] = useState<Series>('viirs')
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([getJson<{ regions: Region[] }>('/api/archive/regions'), getJson<Validation>('/api/archive/validation')])
      .then(([r, v]) => { setRegions(r.regions); setVal(v); const first = r.regions.find((x) => x.available); if (first) setRegion(first.id) })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Archive unavailable'))
  }, [])
  useEffect(() => {
    if (!region) return
    setCal(null)
    getJson<Calendar>(`/api/archive/${region}/calendar`).then(setCal).catch((e: unknown) => setError(e instanceof Error ? e.message : 'Calendar unavailable'))
  }, [region])

  const testFrom = val?.design?.test_years[0] ?? 2020
  const models = val?.models
  const best = val?.selected_model
  const totals = useMemo(() => {
    if (!cal) return null
    const ok = cal.months.filter((m) => m.complete)
    return { months: ok.length, incomplete: cal.months.length - ok.length, viirs: ok.reduce((s, m) => s + (m.viirs ?? 0), 0), modis: ok.reduce((s, m) => s + (m.modis ?? 0), 0) }
  }, [cal])
  const download = () => {
    if (!cal) return
    const header = 'region,month,split,complete,viirs_snpp_vegetation_fire,modis_vegetation_fire,modis_aqua,modis_terra,modis_harmonized_viirs_equivalent,harmonization_model'
    const lines = cal.months.map((m) => [cal.region, m.month, m.split, m.complete, m.viirs ?? '', m.modis ?? '', m.modis_aqua ?? '', m.modis_terra ?? '', m.modis_harmonized ?? '', cal.harmonization_model ?? ''].join(','))
    const url = URL.createObjectURL(new Blob([[header, ...lines].join('\n')], { type: 'text/csv' }))
    const a = document.createElement('a'); a.href = url; a.download = `firesync_calendar_${cal.region}.csv`; a.click(); URL.revokeObjectURL(url)
  }

  return (
    <section className="historical" aria-labelledby="hist-title">
      <div className="section-heading">
        <div><span className="section-kicker">HISTORICAL CALENDAR</span><h2 id="hist-title">Burning activity 2012–2025</h2></div>
        <span className="catalog-meta">FIRMS standard-processing archive · vegetation fires only</span>
      </div>
      <p className="lede small">Monthly vegetation-fire detections (FIRMS type 0) per study region, from the science-quality archive. MODIS is converted to a VIIRS-equivalent count with a model fitted on <b>2012–2019</b> and tested on <b>2020–2025</b>. The test years were never used for fitting or tuning.</p>
      {error && <div className="error-panel">{error}</div>}

      <div className="compare-controls">
        <div className="preset-row">
          {regions.map((r) => (
            <button key={r.id} type="button" disabled={!r.available} className={`preset ${region === r.id ? 'active' : ''}`} onClick={() => setRegion(r.id)}>
              <strong>{r.label}</strong><span>{r.available ? r.regime : 'not downloaded'}</span>
            </button>
          ))}
        </div>
      </div>

      {totals && cal && (
        <div className="stat-row">
          <StatTile label="COMPLETE MONTHS" value={fmt(totals.months)} foot={totals.incomplete ? `${totals.incomplete} incomplete months excluded` : 'every month fully fetched'} />
          <StatTile accent="#3987e5" label="VIIRS S-NPP VEGETATION-FIRE DETECTIONS" value={fmt(totals.viirs)} foot="2012-02 → 2025-12" />
          <StatTile accent="#d95926" label="MODIS VEGETATION-FIRE DETECTIONS" value={fmt(totals.modis)} foot={`VIIRS ÷ MODIS = ${fmt(totals.viirs / Math.max(totals.modis, 1), 2)}`} />
          <StatTile label="HARMONIZATION MODEL" value={cal.harmonization_model ?? '—'} foot={cal.harmonization_model && models ? models[cal.harmonization_model].label : 'needs complete train + test data'} />
        </div>
      )}

      {cal && (
        <div className="chart-card wide">
          <h3>Calendar <small>year × month · hover a cell for counts · hatched = incomplete month (unknown, not zero)</small></h3>
          <div className="layer-toggles series-toggle">
            {SERIES.map((s) => <button key={s.id} type="button" className={`layer-toggle ${series === s.id ? 'checked' : ''}`} onClick={() => setSeries(s.id)}>{s.label}</button>)}
          </div>
          <Heatmap rows={cal.months} series={series} testFrom={testFrom} />
          <p className="chart-note">{series === 'ratio' ? 'Orange = harmonized MODIS above observed VIIRS, blue = below, grey = equal (scale ±2×).' : 'Colour is log-scaled, so a step in colour is a multiplicative change in detections.'}</p>
          <div className="export-row"><button type="button" className="ghost-button" onClick={download}>Download monthly series (CSV)</button><span>Observed counts are archive totals. Harmonized values are model estimates.</span></div>
        </div>
      )}
      {cal && <div className="chart-card wide"><h3>Monthly detections <small>observed VIIRS vs MODIS converted to VIIRS-equivalent</small></h3><TimeSeries rows={cal.months} testFrom={testFrom} /></div>}

      {val && val.status !== 'ok' && <div className="map-warning">{val.reason ?? 'Validation needs complete training (2012–2019) and test (2020–2025) months.'} Currently {fmt(val.train_months ?? 0)} training and {fmt(val.test_months ?? 0)} test months are available. Downloads may still be running.</div>}
      {val?.status === 'ok' && models && best && (
        <div className="chart-card wide">
          <h3>Year-based validation <small>fit 2012–2019 · test 2020–2025 · regions: {val.regions?.join(', ')} · {fmt(val.months?.train)} train / {fmt(val.months?.test)} test region-months</small></h3>
          <table className="data-table">
            <thead><tr><th>Model</th><th>Training CV |error|</th><th>Test median |error|</th><th>Bias</th><th>Annual total error</th><th>Spearman</th><th>Anomaly sign agreement</th><th>…large anomalies</th></tr></thead>
            <tbody>
              {Object.entries(models).map(([id, m]) => (
                <tr key={id} className={id === best ? 'best-row' : ''}>
                  <td>{id} · {m.label}{id === best ? ' ★ selected' : ''}</td>
                  <td>×{fmt(10 ** (val.selection?.cv_median_abs_log10_error_by_model[id] ?? 0), 2)}</td>
                  <td>×{fmt(m.test.median_abs_error_factor, 2)}</td>
                  <td>{m.test.bias_log10 >= 0 ? '+' : ''}{fmt((10 ** m.test.bias_log10 - 1) * 100, 0)}%</td>
                  <td>{fmt(m.test.annual_total_median_abs_pct_error, 0)}%</td>
                  <td>{fmt(m.test.spearman_monthly, 2)}</td>
                  <td>{m.anomaly_agreement_test.sign_agreement === null ? '—' : `${fmt(m.anomaly_agreement_test.sign_agreement * 100, 0)}%`}</td>
                  <td>{m.anomaly_agreement_test.sign_agreement_large_anomalies === null ? '—' : `${fmt(m.anomaly_agreement_test.sign_agreement_large_anomalies * 100, 0)}% of ${m.anomaly_agreement_test.large_anomaly_months}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="chart-note">Median |error| is the typical multiplicative error of a monthly count, so ×1.25 means within 25% for half the months. Bias is the median over- or under-estimate. Anomaly agreement asks whether harmonized MODIS and VIIRS agree that a month is above or below its 2012–2019 normal. "Large" means VIIRS is at least 50% off normal. ★ marks the model selected by leave-one-year-out cross-validation on 2012–2019 only. Test errors are reported but never used for the choice. On the test years the selected model ({best}) {val.selected_beats_no_harmonization_on_test ? 'beats' : 'does NOT beat'} no harmonization{val.selected_beats_pooled_ratio_on_test === null ? '' : ` and ${val.selected_beats_pooled_ratio_on_test ? 'beats' : 'does NOT beat'} the pooled ratio`}. Lowest test error (diagnostic only, not used for selection): {val.lowest_test_error_model_diagnostic_only}. The seasonal shrinkage λ = {val.design?.chosen_lambda} was chosen by leave-one-year-out cross-validation on 2012–2019.</p>
          <h4 className="sub">Test error by year <small>shows whether errors grow over time (possible causes: Terra/Aqua orbit drift after ~2020, and the MODIS 6.03 → 61.03 processing change in Jan 2023; not separated here)</small></h4>
          <table className="data-table compact">
            <thead><tr><th>Year</th>{['M1', 'M2', best].filter((v, i, a) => a.indexOf(v) === i).map((m) => <th key={m}>{m} median |error|</th>)}<th>Observed VIIRS ÷ MODIS{val.regions && val.regions.length > 1 ? ' (per region)' : ''}</th></tr></thead>
            <tbody>
              {Object.keys(models[best].test_by_year).map((y) => (
                <tr key={y}>
                  <td>{y}</td>
                  {['M1', 'M2', best].filter((v, i, a) => a.indexOf(v) === i).map((m) => <td key={m}>×{fmt(models[m].test_by_year[y]?.median_abs_error_factor, 2)}</td>)}
                  <td>{val.ratio_viirs_over_modis_by_year?.filter((r) => String(r.year) === y).map((r) => `${r.region}: ${fmt(r.ratio, 2)}`).join(' · ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
