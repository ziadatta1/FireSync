# FireSync scientific methodology

FireSync compares active-fire detections from three NASA FIRMS sources while keeping
them separate. This document explains what is computed, why, what was validated, and
what is still experimental. Every number below is reproducible with:

```sh
backend/.venv/bin/python backend/scripts/run_comparison.py   # writes docs/comparison_results.json
```

## 1. Data

| Dataset | FIRMS source | Instrument / platforms | Nominal pixel | Rows | UTC dates |
|---|---|---|---|---:|---|
| `modis` | `MODIS_NRT` (C6.1) | MODIS on Terra (`T`) + Aqua (`A`) | ~1 km | 81,258 | 2026-11-06 → 2026-11-13* |
| `noaa20` | `VIIRS_NOAA20_NRT` (C2) | VIIRS I-band on NOAA-20 (`N20`) | 375 m | 416,571 | 2026-11-06 → 2026-11-13* |
| `noaa21` | `VIIRS_NOAA21_NRT` (C2) | VIIRS I-band on NOAA-21 (`N21`) | 375 m | 422,644 | 2026-11-06 → 2026-11-13* |

\* 2026-11-13 is a partial day (the snapshot was taken during that UTC day; NOAA-20 ends earlier that day than NOAA-21).
No duplicates, invalid coordinates, or missing values in any file. Provenance: see
[`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md).

**Unit of observation.** A row is one satellite pixel flagged as containing a thermal
anomaly (a *detection*). One fire produces many detections, on several overpasses, from
several satellites. FireSync therefore never sums detections across sources into a "number
of fires", and the UI labels every count as detections.

**Confidence.** VIIRS reports `low / nominal / high`. MODIS reports 0–100; FireSync maps it to
the FIRMS classes `<30` low, `30–79` nominal, `≥80` high for filtering only. The original value
is always preserved and exported.

## 2. Common spatial grid

Detections are binned on a regular latitude/longitude grid (0.05°, 0.1°, 0.25°, 0.5°, 1°, 2°).
Cells are **analysis bins, not sensor footprints**: a 0.1° cell (~11 km at the equator) holds
roughly 100 MODIS or 800 VIIRS nadir pixels, and pixel footprints grow toward the swath edge
(the `scan`/`track` columns). Aggregation does not make the sensors' sampling identical; it
makes their detections comparable at a stated scale. Cell area also shrinks with latitude, so
cell counts are not densities.

The map uses the grid only for display (2° at world zoom down to 0.1°) and switches to
individual detections at zoom ≥ 7. Above 8,000 detections per source in view, it shows the
highest-FRP 8,000 and says so.

## 3. Temporal alignment: cell-overpasses

Within one cell, a sensor's detections less than 20 minutes apart are merged into a single
**cell-overpass**: one look by one satellite at one cell. Its FRP is the sum of its pixels'
FRP (FRP is additive power, MW), and its pixel count is kept.

Cell-overpasses of sensor A are matched **one-to-one** to the nearest-in-time cell-overpass
of sensor B in the same cell, if |Δt| ≤ window. Matching is greedy by smallest |Δt|, so no
B overpass is used twice.

The data itself shows how far apart the satellites look (all 0.1° co-detections, ±5 h search):

| Pair | Observed Δt of same-cell looks |
|---|---|
| NOAA-20 ↔ NOAA-21 | 30–60 min, both orders (half-orbit separation in the same orbital plane) |
| Aqua MODIS ↔ NOAA-20/21 | mostly 90–180 min (VIIRS earlier), median ≈ 130 min |
| Terra MODIS ↔ NOAA-21 | 3–5 h |

The drifting Aqua and Terra orbits mean **MODIS and VIIRS do not observe simultaneously** in
this period. Only NOAA-20 ↔ NOAA-21 is near-simultaneous.

## 4. Non-detection vs. no observation

FIRMS detection files list fires only. On its own, an unmatched cell-overpass (A saw fire,
B did not) could mean: B was not looking there, B looked but clouds blocked it, or B looked
and detected nothing (a fire below its detection limit, or one that changed).

### Swath footprints (primary method)

FireSync separates "not looking" from "looked, no detection" using **swath footprints**. These
are the outline polygons of every Level-2 active-fire granule (6-minute VIIRS, 5-minute MODIS),
the same data behind the FIRMS "Satellite Swath Outlines" layer. They are fetched from NASA's
public Common Metadata Repository by `backend/scripts/fetch_swaths.py`:

| Dataset | Collections | Granules | Catalogue starts |
|---|---|---:|---|
| `modis` | `MOD14` 6.1NRT (Terra), `MYD14` 6.1NRT (Aqua) | 2,068 + 2,068 | 2026-11-06 14:05 / 14:15 UTC |
| `noaa20` | `VJ114IMG_NRT` v2 | 1,731 | 2026-11-06 01:54 UTC |
| `noaa21` | `VJ214IMG_NRT` v2 | 1,721 | 2026-11-06 02:54 UTC |

Each footprint is a 4-corner GEODETIC polygon (great-circle edges, convex, counter-clockwise).
Containment is tested on the unit sphere, so granules crossing the antimeridian or the poles
are handled exactly. FIRMS acquisition times are truncated to the minute, so ±3 minutes of
slack lets a detection match a neighbouring granule; each granule is still tested with its
own polygon.

**Validation.**
- **Own detections:** every catalogued detection of every satellite falls inside one of its
  own footprints. That is 100% of 46,978 Aqua, 26,723 Terra, 385,903 NOAA-20 and 381,656
  NOAA-21 detections.
- **Negative control:** moving the same detections 180° in longitude puts 0% inside, and
  moving them 30° east puts about 1% inside.
- **Area:** one granule covers about 7.4 million km² (VIIRS) or 4.9 million km² (MODIS),
  consistent with swath width × granule duration.

**Classification.** An unmatched A cell-overpass at time t is put in one category:

- **"In B's swath, no detection"**: a B granule overlapping [t − window, t + window] had the
  fire location (mean of A's detections) in view.
- **"Not in B's swath"**: the catalogue is complete for the whole window, and no B granule had
  the location in view.
- **"B catalogue gap (unknown)"**: not in view of any catalogued granule, and the window is
  not fully catalogued. NRT granules age out of the catalogue (the first ~2–14 hours of
  6 Nov are missing) and a few granules are absent. Inside a gap FireSync cannot say whether
  B looked.
- **"B look already paired"**: B's nearest look at this cell was matched one-to-one to
  another A look. Reported separately, so it is neither a miss nor a hit.

```
conditional co-detection rate (A→B) = co-detected / (co-detected + in B's swath, no detection)
```

"Not in swath", "unknown" and "already paired" never count toward the rate, and "no
detection" is never presented as "no fire".

**What footprints do not show.** A footprint says the location was within view, not that it
was cloud-free, so some "in swath, no detection" cases are clouds. NASA also notes the
outlines are imprecise at swath edges. The own-detection validation found no misses there,
but a location just outside a true edge could still be counted as in view. Co-detection
rates are therefore conservative with respect to clouds, but not a strict bound.

### Coarse-cell proxy (fallback)

If a dataset has no footprints, FireSync falls back to the earlier proxy: B counts as having
viewed the area if it detected anything in the same 1° cell within the window. The API
reports which method each side used (`coverage_method`).

**The proxy is biased.** It only recognises B as looking where B also detected fire nearby.
With footprints, the NOAA-21 → Aqua co-detection rate falls from 0.26 (proxy) to 0.14. For
NOAA-20 ↔ NOAA-21 the rates barely change (0.56 → 0.555).

## 5. FRP comparison and calibration

For matched pairs with FRP > 0 on both sides, FireSync reports:

- Pearson r and Spearman ρ of log10 cell-overpass FRP.
- Median ratio B/A, with a 95% bootstrap CI.
- An **overpass-order test**: the median B/A ratio computed separately for pairs where A
  looked first and pairs where B looked first. If a difference came from fires growing or
  fading between looks, the ratio would shift with order. An order-independent ratio points
  to a measurement difference between the sensors.

**Calibration** is attempted only if the median |Δt| of the pairs is ≤ 60 min. The model is:

```
log10 FRP_B = intercept + slope · log10 FRP_A        (reduced major axis)
```

Reduced major axis treats both sensors as error-bearing; ordinary least squares would bias
the slope toward 0. The fit uses the first 60% of dates and is evaluated on the later,
held-out dates against the **1:1 (no calibration) baseline**. Slope and intercept carry
bootstrap CIs (400 resamples). At least 30 pairs are needed in both training and validation.

## 6. Results (global, all dates)

| Pair | Grid | Window | Pairs | median abs Δt | co-det A→B* | co-det B→A* | log-FRP r / ρ | median FRP B/A [95% CI] | Calibration | held-out median abs error (log10): fit vs 1:1 |
|---|---|---|---:|---|---|---|---|---|---|---|
| NOAA-20 ↔ NOAA-21 | 0.05° | ±60 | 76,741 | 45 min | 0.495 | 0.490 | 0.70 / 0.67 | 1.175 [1.165, 1.185] | slope 0.978 [0.972, 0.985] | 0.280 vs 0.288 |
| NOAA-20 ↔ NOAA-21 | **0.1°** | ±60 | 68,494 | 45 min | 0.555 | 0.549 | 0.72 / 0.69 | 1.176 [1.165, 1.185] | slope 0.980 [0.975, 0.986] | 0.278 vs 0.286 |
| NOAA-20 ↔ NOAA-21 | 0.25° | ±60 | 50,711 | 45 min | 0.643 | 0.635 | 0.77 / 0.75 | 1.172 [1.161, 1.186] | slope 0.982 [0.977, 0.989] | 0.281 vs 0.287 |
| Aqua MODIS ↔ NOAA-20 | 0.1° | ±180 | 13,925 | 128 min | 0.588 | 0.138 | 0.46 / 0.43 | 0.98 [0.96, 1.01] | **refused** (not simultaneous) | — |
| Aqua MODIS ↔ NOAA-21 | 0.1° | ±180 | 13,718 | 131 min | 0.566 | 0.141 | 0.46 / 0.42 | 1.16 [1.13, 1.20] | **refused** (not simultaneous) | — |
| MODIS ↔ NOAA-20 (strict) | 0.1° | ±60 | 282 | 55 min | 0.348 | 0.061 | 0.56 / 0.47 | 0.64 [0.55, 0.77] | fitted, **worse than 1:1** | 0.291 vs 0.184 |
| MODIS ↔ NOAA-21 (strict) | 0.1° | ±60 | 125 | 58 min | 0.291 | 0.063 | 0.55 / 0.51 | 0.78 [0.65, 0.86] | insufficient data | — |

\* Co-detection rates use swath footprints (§4). The FRP columns do not depend on the
coverage method.

Overpass-order test, NOAA-20 ↔ NOAA-21, 0.1°: NOAA-21/NOAA-20 = **1.179** [1.167, 1.191]
when NOAA-20 looked first (39,890 pairs) and **1.171** [1.156, 1.189] when NOAA-21 looked
first (28,603 pairs).

### Interpretation

1. **NOAA-20 and NOAA-21 agree closely, with a stable offset.** Paired cell-overpass FRP
   correlates at r ≈ 0.72 in log space, and the slope is ≈ 0.98 at every grid size tested.
   NOAA-21 reads ≈ 17% higher FRP than NOAA-20 for the same cells. That offset does not
   depend on which satellite looked first, so fire evolution between looks cannot explain
   it. The difference is consistent with an inter-sensor (or processing) difference in this
   NRT period.
2. **Calibrating NOAA-20 to NOAA-21 is barely worthwhile.** On held-out dates the fit reduces
   median absolute error by only ~3% (0.278 vs 0.286 log10, roughly a factor of 1.9 vs 1.93).
   The remaining scatter, about a factor of 2 per cell-overpass, comes from fire variability
   over 45 minutes and from viewing geometry, not from a correctable bias. FireSync reports
   the relationship but does **not** apply a correction to the data.
3. **MODIS ↔ VIIRS cannot be calibrated with this data.** Aqua is typically 2+ hours from
   VIIRS. The few truly close pairs (≤ 60 min) yield a fit that is worse than 1:1 on held-out
   dates, and FireSync labels it that way. Their detections can still be compared spatially
   and in counts, but not as simultaneous measurements.
4. **Detection asymmetry is expected.**
   - **VIIRS confirms MODIS:** when an Aqua MODIS fire location was within a VIIRS swath
     (±3 h), VIIRS also detected fire in that cell about 57–59% of the time.
   - **MODIS confirms VIIRS far less:** when a VIIRS fire location was within an Aqua swath,
     Aqua detected fire there only about 14% of the time.
   - **Likely reasons:** VIIRS's smaller pixels pick up smaller, cooler fires below the MODIS
     detection limit, and fires change over the 2+ hours between looks. Do not read this as a
     VIIRS false-alarm rate.
   - **Even near-simultaneous VIIRS pairs disagree a lot:** the two VIIRS satellites, about
     45 min apart and both in view, co-detect only about 55% of each other's fire looks. Short
     fire lifetimes, clouds and detection near the threshold limit agreement.
5. **Agreement grows with coarser cells** (co-detection 0.49 → 0.64 from 0.05° to 0.25°),
   as expected when geolocation offsets and fire-front motion are absorbed. The FRP ratio and
   slope stay stable, so the FRP conclusions do not depend on grid choice.

## 7. What is validated, and what is experimental

**Validated (tested, reproducible, with held-out evidence):**
- Dataset integrity (SHA-256, row counts) and authenticity cross-checks against direct FIRMS
  API retrievals.
- Grid aggregation preserves all detections (unit-tested). Export rows exactly match the
  source rows (unit-tested and checked live: 78,172 rows for a test polygon equal the stats
  total).
- The pairing algorithm recovers a known synthetic relationship (slope 1, ×2 offset, 50-min
  lag) and refuses calibration for a 3-hour lag (unit tests).
- The NOAA-20 ↔ NOAA-21 relationship and its order-independence on held-out dates.
- Swath-footprint geometry and timing. 100% of 841,260 catalogued own-satellite detections
  fall inside their own footprints, and the negative control gives 0%. Spherical containment
  is unit-tested across the antimeridian and the pole, and catalogue gaps are unit-tested to
  give "unknown".

**Experimental / limitations:**
- Swath footprints show what was in view, not cloud cover. Some "in swath, no detection"
  cases are clouds, and outlines are approximate at swath edges.
- Footprints are missing for the first hours of 6 Nov and for a few granules. Those looks
  are reported as "unknown".
- Bootstrap CIs resample pairs independently and understate uncertainty from spatial and
  temporal autocorrelation. With only 8 days of data, a block-by-day bootstrap is not yet
  meaningful.
- Pairs are conditional on both sensors detecting, which biases them toward larger fires.
- NRT products only. Standard-processing (SP) data may revise detections and FRP.
- Results describe 6–13 November 2026 and the regions burning then (dominated by the
  African savanna burning season). They are not a global transfer function.
- User-drawn areas (planar point-in-polygon) and lat/lon boxes do not cross the antimeridian.
  Footprint tests do.
- No multi-year archive is loaded, so there is no historical calendar, seasonal baseline, or
  anomaly detection, and none is simulated.

## 8. Relationship to the earlier "Emberline" prototype

The earlier prototype (`~/Downloads/FireSync_Emberline_Handoff`) was inspected and **not
reused**:

- It calibrated with latitude-band ratios of totals rather than paired observations.
- It treated unobserved cells as zeros, which inflated the harmonized totals.
- Its historical calendar was simulated.

FireSync replaces it with the paired, held-out-validated comparison above. It deliberately
delivers a **comparison product, not a merged "harmonized" dataset**, because the data does
not support averaging across sensors that don't observe simultaneously.
