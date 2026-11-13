import { useMemo, useState, type ReactNode } from 'react'
import { fmt } from './api.ts'

export type Series = { key: string; label: string; color: string; values: (number | null)[] }

function niceMax(value: number) {
  if (value <= 0) return 1
  const exp = Math.pow(10, Math.floor(Math.log10(value)))
  const n = value / exp
  const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((candidate) => n <= candidate) ?? 10
  return step * exp
}

function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null
  return (
    <div className="chart-legend">
      {series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
    </div>
  )
}

/** Grouped vertical bars (one group per category). Null = no data for that source/category. */
export function GroupedBars({
  categories, series, height = 190, width = 640, valueLabel, formatCategory = (c) => c, note,
}: {
  categories: string[]; series: Series[]; height?: number; width?: number; valueLabel: string; formatCategory?: (c: string) => string; note?: ReactNode
}) {
  const [hover, setHover] = useState<number | null>(null)
  const pad = { l: width < 500 ? 44 : 52, r: 8, t: 10, b: 28 }
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values.map((v) => v ?? 0))))
  const innerW = width - pad.l - pad.r
  const innerH = height - pad.t - pad.b
  const groupW = innerW / Math.max(categories.length, 1)
  const barW = Math.max(2, Math.min(22, (groupW - 6) / Math.max(series.length, 1) - 2))
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max)
  const labelEvery = Math.ceil(categories.length / (width < 500 ? 7 : 12))
  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <Legend series={series} />
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={valueLabel}>
        {ticks.map((t) => {
          const y = pad.t + innerH - (t / max) * innerH
          return <g key={t}><line x1={pad.l} x2={width - pad.r} y1={y} y2={y} className="grid" /><text x={pad.l - 6} y={y + 3} className="tick" textAnchor="end">{fmt(t, t < 10 && max < 10 ? 1 : 0)}</text></g>
        })}
        {categories.map((category, i) => {
          const gx = pad.l + i * groupW
          const totalW = series.length * (barW + 2) - 2
          const start = gx + (groupW - totalW) / 2
          return (
            <g key={category} onMouseEnter={() => setHover(i)}>
              <rect x={gx} y={pad.t} width={groupW} height={innerH} className={`hit ${hover === i ? 'active' : ''}`} />
              {series.map((s, k) => {
                const value = s.values[i]
                if (value === null || value === undefined) {
                  return <text key={s.key} x={start + k * (barW + 2) + barW / 2} y={pad.t + innerH - 3} className="nodata" textAnchor="middle">·</text>
                }
                const h = (value / max) * innerH
                const r = Math.min(3, barW / 2, h)
                const x = start + k * (barW + 2)
                const y = pad.t + innerH - h
                return <path key={s.key} fill={s.color} d={`M${x},${pad.t + innerH} V${y + r} Q${x},${y} ${x + r},${y} H${x + barW - r} Q${x + barW},${y} ${x + barW},${y + r} V${pad.t + innerH} Z`} />
              })}
              {i % labelEvery === 0 && <text x={gx + groupW / 2} y={height - 10} className="tick" textAnchor="middle">{formatCategory(category)}</text>}
            </g>
          )
        })}
        <line x1={pad.l} x2={width - pad.r} y1={pad.t + innerH} y2={pad.t + innerH} className="axis" />
      </svg>
      {hover !== null && (
        <div className="chart-tooltip" style={{ left: `${((pad.l + (hover + 0.5) * groupW) / width) * 100}%` }}>
          <strong>{formatCategory(categories[hover])}</strong>
          {series.map((s) => <div key={s.key}><i style={{ background: s.color }} />{s.label}<b>{s.values[hover] === null ? 'no data' : fmt(s.values[hover] ?? 0, 1)}</b></div>)}
        </div>
      )}
      {note && <p className="chart-note">{note}</p>}
    </div>
  )
}

/** log10-log10 scatter with identity line and optional fitted line. */
export function LogScatter({
  points, xLabel, yLabel, fit, color = '#3987e5',
}: { points: [number, number][]; xLabel: string; yLabel: string; fit?: { slope: number; intercept: number } | null; color?: string }) {
  const [hover, setHover] = useState<[number, number] | null>(null)
  const size = 340
  const pad = 44
  const [lo, hi] = useMemo(() => {
    if (!points.length) return [0, 2]
    const all = points.flat()
    return [Math.floor(Math.min(...all)), Math.ceil(Math.max(...all))]
  }, [points])
  const scale = (v: number) => pad + ((v - lo) / (hi - lo || 1)) * (size - pad - 10)
  const scaleY = (v: number) => size - pad - ((v - lo) / (hi - lo || 1)) * (size - pad - 10)
  const decades = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  return (
    <div className="chart scatter">
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${yLabel} versus ${xLabel}`} onMouseLeave={() => setHover(null)}>
        {decades.map((d) => (
          <g key={d}>
            <line x1={scale(d)} x2={scale(d)} y1={10} y2={size - pad} className="grid" />
            <line x1={pad} x2={size - 10} y1={scaleY(d)} y2={scaleY(d)} className="grid" />
            <text x={scale(d)} y={size - pad + 14} className="tick" textAnchor="middle">{fmt(10 ** d, d < 0 ? 1 : 0)}</text>
            <text x={pad - 6} y={scaleY(d) + 3} className="tick" textAnchor="end">{fmt(10 ** d, d < 0 ? 1 : 0)}</text>
          </g>
        ))}
        {points.map(([x, y], i) => (
          <circle key={i} cx={scale(x)} cy={scaleY(y)} r={2.2} fill={color} fillOpacity={0.35} onMouseEnter={() => setHover([x, y])} />
        ))}
        <line x1={scale(lo)} y1={scaleY(lo)} x2={scale(hi)} y2={scaleY(hi)} className="identity" />
        {fit && Number.isFinite(fit.slope) && (
          <line x1={scale(lo)} y1={scaleY(fit.intercept + fit.slope * lo)} x2={scale(hi)} y2={scaleY(fit.intercept + fit.slope * hi)} className="fitline" />
        )}
        <text x={size / 2} y={size - 8} className="axis-label" textAnchor="middle">{xLabel}</text>
        <text x={12} y={size / 2} className="axis-label" textAnchor="middle" transform={`rotate(-90 12 ${size / 2})`}>{yLabel}</text>
      </svg>
      {hover && <div className="chart-tooltip static">A {fmt(10 ** hover[0], 1)} MW · B {fmt(10 ** hover[1], 1)} MW</div>}
      <div className="chart-legend"><span><i className="dash identity-key" />1 : 1 (no calibration)</span>{fit && <span><i className="dash fit-key" />RMA fit (training dates)</span>}</div>
    </div>
  )
}

export function StatTile({ label, value, foot, accent }: { label: string; value: ReactNode; foot?: ReactNode; accent?: string }) {
  return (
    <article className="stat-tile">
      <span className="stat-label">{accent && <i style={{ background: accent }} />}{label}</span>
      <strong>{value}</strong>
      {foot && <span className="stat-foot">{foot}</span>}
    </article>
  )
}
