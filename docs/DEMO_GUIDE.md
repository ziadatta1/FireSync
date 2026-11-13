# FireSync — 5-minute demo guide

**Before going on stage:**
1. Start the API and the frontend as in the README.
2. Open <http://127.0.0.1:5173> and wait for the header to show **920,473 NASA FIRMS DETECTIONS**.
3. Keep the browser full-screen. The basemap needs internet access.

## 1. The problem (30 s)

Three satellites see the same fires differently:

- MODIS has ~1 km pixels and flies on the ageing Terra and Aqua.
- VIIRS on NOAA-20 and NOAA-21 has 375 m pixels.
- Suomi NPP ended deliveries this month, so NOAA-21 and NOAA-20 now carry VIIRS.

Can their observations be combined? FireSync tests that honestly instead of assuming it.

## 2. Global map (60 s) — *Global map & area analysis* tab

- "Every mark is real NASA FIRMS data from the last 7 days: 81k MODIS, 417k NOAA-20 and 423k
  NOAA-21 detections."
- Point to the legend. Each source has its own color **and** shape: MODIS ■, NOAA-20 ●,
  NOAA-21 ◆.
- Scroll-zoom into Central or East Africa. The cells refine (2° → 1° → 0.5° → 0.25° → 0.1°),
  computed on the server, so the browser never draws 900k markers.
- Keep zooming to individual detections (zoom ≥ 7). Click a ◆ to show its full source
  record: FIRMS source, row number, scan time, FRP, confidence, and the "cross-checked"
  provenance label.
- Toggle a source off and on. Change **Min confidence → High** to show the filters.

## 3. Area analysis (60 s)

- Click **▭ Box** and drag over a hotspot. Or type the coordinates South 0, West 15,
  North 12, East 32 (the Central African savanna belt) and press **Set area**.
- Scroll down:
  - Per-satellite totals: "detections, not fires".
  - Daily detections by satellite. The last day is partial.
  - Hour-of-day histogram: "this shows *when satellites fly over*, not when fires burn".
  - FRP distribution. MODIS pixel FRP is larger because its pixels are about 7× bigger.
  - Top active cells: click one to fly there.
- **Export … source rows (CSV)**: the rows are unchanged, with the dataset, file SHA-256 and
  row number attached.

## 4. Sensor comparison — the science (90 s) — *Sensor comparison* tab

- Preset **NOAA-20 ↔ NOAA-21**: "We pair each satellite's look at an 11 km cell with the
  other satellite's nearest look at the same cell within ±60 min."
- Detection agreement bars:
  - Green: both detected it.
  - Grey: the other satellite was imaged nearby but saw nothing in this cell.
  - Hatched: we can't tell whether it looked.

  "We never count *unknown* as disagreement, and we never call a non-detection 'no fire'."
- Time-offset histogram: the two satellites look about 45 min apart, from both sides.
- Scatter and **overpass-order test**: "NOAA-21 reads about 17% higher FRP than NOAA-20, and
  it's the same whichever satellite looked first. So it's a sensor difference, not the fire
  growing."
- Calibration card: "Fitted on 6–10 Nov, tested on 11–13 Nov. The slope is about 0.98, and
  it beats 1:1 by only about 3%. Honest verdict: NOAA-20 and NOAA-21 are already consistent
  to within a constant factor."
- Click preset **MODIS Aqua ↔ NOAA-21**: the median offset is 131 min, and the calibration
  is **refused**. "Aqua's orbit has drifted, so MODIS and VIIRS don't observe at the same
  time. Fitting a correction here would be fabricating precision."

## 5. Provenance and honesty (30 s) — *Data & provenance* tab

- SHA-256 per file. All rows from today's direct FIRMS API pulls match the downloads exactly
  (100% for granules present in both).
- "We show 8 real days and say so. No simulated 20-year calendar."
- Scroll down: the original FireSync map, still working.

## Likely questions

- **Why not merge into one harmonized product?** The sensors don't observe simultaneously,
  and an earlier ratio-based attempt inflated totals. We deliver a validated comparison
  instead. The NOAA-20/21 relationship is ready to apply if a team chooses to.
- **How do you know the downloads are real?** They were independently cross-checked row by
  row against the FIRMS API with our own MAP_KEY. See `DATA_PROVENANCE.md`.
- **Performance?** About 920k rows sit in numpy arrays. A global grid takes about 90 ms, and
  a full pairing comparison about 1.6 s (then it's cached).

## If something fails live

- **Map is blank but the header shows counts.** The basemap can't load (no internet). The
  data layer still draws over a dark background.
- **The header says API OFFLINE.** Restart the API; see the README quick start.
