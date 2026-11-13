export default function Methodology() {
  return (
    <section className="method" aria-labelledby="method-title">
      <div className="section-heading">
        <div><span className="section-kicker">METHODOLOGY</span><h2 id="method-title">What FireSync does — and does not — claim</h2></div>
        <span className="catalog-meta">Full text: docs/METHODOLOGY.md</span>
      </div>
      <div className="method-grid">
        <article>
          <h3>1 · Sources kept separate</h3>
          <p>MODIS (Terra + Aqua, ~1 km), VIIRS NOAA-20 and VIIRS NOAA-21 (375 m) are stored and served as three datasets. Rows are never merged, averaged, or rescaled. Suomi NPP is retained only as historical data.</p>
        </article>
        <article>
          <h3>2 · Detections, not fires</h3>
          <p>Each row is a satellite pixel flagged as a thermal anomaly. One fire produces many detections, and both VIIRS satellites see it, so detection totals are never summed into “number of fires”.</p>
        </article>
        <article>
          <h3>3 · Common grid for display & comparison</h3>
          <p>Detections are binned on a regular lat/lon grid (0.05°–2°). Cells are analysis bins, not sensor footprints: a 0.1° cell (~11 km) holds ~100 MODIS or ~800 VIIRS nadir pixels.</p>
        </article>
        <article>
          <h3>4 · Temporal alignment</h3>
          <p>Each sensor's detections in a cell within 20 min form one <em>cell-overpass</em>. Overpasses are matched one-to-one to the other sensor's nearest look within ±window. The data shows NOAA-20 and NOAA-21 look ~30–60 min apart; Aqua MODIS is typically 1.5–3 h from VIIRS and Terra 3–5 h.</p>
        </article>
        <article>
          <h3>5 · Non-detection ≠ no observation</h3>
          <p>FIRMS lists detections only. FireSync adds NASA swath footprints (one outline per 5–6 min granule, from the public CMR catalogue) to tell “in view, no detection” from “not in view”. Catalogue gaps are “unknown”. 100% of each satellite's own detections fall inside its own footprints. Footprints show what was in view, not clouds. Only in-view cases count toward agreement rates.</p>
        </article>
        <article>
          <h3>6 · Calibration only when defensible</h3>
          <p>FRP is compared as summed cell-overpass FRP in log space. A reduced-major-axis fit is estimated on the first 60% of dates and validated on later held-out dates against a 1:1 baseline. It is refused when paired looks are &gt; 60 min apart (fires change on that time scale).</p>
        </article>
        <article>
          <h3>7 · Why no merged “harmonized” product</h3>
          <p>An earlier prototype applied latitude-band ratios of totals and treated missing observations as zeros. FireSync replaces that with a transparent comparison product. The validated NOAA-20/NOAA-21 relationship is close to 1:1, and MODIS–VIIRS pairs are not simultaneous enough to calibrate.</p>
        </article>
        <article>
          <h3>8 · Provenance</h3>
          <p>Each file carries a sidecar with SHA-256, row counts, date range and an independent cross-check against rows retrieved directly from the FIRMS API. Every export row carries its dataset, FIRMS source, file hash and source row number.</p>
        </article>
      </div>
    </section>
  )
}
