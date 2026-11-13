import { SOURCES, fmt, type GlobalDataset } from './api.ts'
import WildfireMap from './WildfireMap.tsx'

export default function DataPanel({ datasets, error }: { datasets: GlobalDataset[]; error: string }) {
  return (
    <section aria-labelledby="data-title">
      <div className="section-heading">
        <div><span className="section-kicker">DATA &amp; PROVENANCE</span><h2 id="data-title">Registered global datasets</h2></div>
        <span className="catalog-meta">Kept separate · never merged · never modified</span>
      </div>
      {error && <div className="error-panel">Dataset catalog unavailable: {error}</div>}
      <div className="dataset-grid global-datasets">
        {datasets.map((d) => {
          const check = d.api_cross_checks[d.api_cross_checks.length - 1]
          const days = Object.entries(d.acquisition_dates)
          const maxDay = Math.max(...days.map(([, n]) => n))
          return (
            <article className="dataset-card" key={d.id}>
              <div className="dataset-top"><span className="dataset-index"><i className={`shape-key ${SOURCES[d.id].shape}`} style={{ background: SOURCES[d.id].color }} />{d.firms_source}</span><span className="dataset-status verified">{d.classification}</span></div>
              <div className="dataset-title-row"><h3>{SOURCES[d.id].label}</h3></div>
              <div className="dataset-file">{d.product} · nominal pixel {d.nominal_pixel_km} km</div>
              <div className="dataset-rule" />
              <div className="dataset-stats">
                <div><span>DETECTIONS</span><strong>{fmt(d.row_count)}</strong></div>
                <div><span>ACQUISITION DATES (UTC)</span><strong className="date-value">{d.date_range.start} — {d.date_range.end}</strong></div>
              </div>
              <div className="day-strip" aria-label="Detections per acquisition date">
                {days.map(([day, n]) => (
                  <div key={day} title={`${day}: ${n.toLocaleString()} detections`}><span style={{ height: `${(n / maxDay) * 100}%`, background: SOURCES[d.id].color }} /><small>{day.slice(8)}</small></div>
                ))}
              </div>
              <dl className="prov-list">
                <div><dt>Original file</dt><dd>{d.original_filename}</dd></div>
                <div><dt>SHA-256</dt><dd className="mono wrap">{d.sha256}</dd></div>
                <div><dt>Satellites</dt><dd>{Object.entries(d.satellite_codes).map(([k, v]) => `${k}: ${fmt(v)}`).join(' · ')}</dd></div>
                <div><dt>Version</dt><dd>{Object.keys(d.versions).join(', ')}</dd></div>
                <div><dt>Quality checks</dt><dd>{d.duplicate_rows} duplicates · {d.invalid_rows} invalid rows · {Object.keys(d.missing_values).length} columns with missing values</dd></div>
                <div><dt>Extent</dt><dd>{d.bounds.south.toFixed(1)}° to {d.bounds.north.toFixed(1)}° lat · {d.bounds.west.toFixed(1)}° to {d.bounds.east.toFixed(1)}° lon</dd></div>
                {check && <div><dt>API cross-check</dt><dd>{fmt(check.matched_within_shared_granules)} / {fmt(check.api_rows_in_granules_present_in_download)} rows from an independent FIRMS API retrieval ({check.retrieved_at}) found exactly (lat, lon, date, time) in this file, for granules present in both.</dd></div>}
              </dl>
              <p className="dataset-notes">{d.acquisition} {d.notes.join(' ')}</p>
            </article>
          )
        })}
      </div>
      <div className="notice"><span className="notice-icon">i</span><p><strong>Historical coverage.</strong> The registered global data spans 8 UTC dates (2026-11-06 to 2026-11-13, last day partial). No multi-year archive is loaded, so FireSync shows no long-term calendar or anomaly baseline rather than a simulated one. Archive (SP) sources can be added through the FIRMS importer.</p></div>

      <div className="legacy-block">
        <div className="section-heading">
          <div><span className="section-kicker">FOUNDATION</span><h2>Local files &amp; FIRMS API imports</h2></div>
          <span className="catalog-meta">Original FireSync map, unchanged</span>
        </div>
        <WildfireMap />
      </div>
    </section>
  )
}
