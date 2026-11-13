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

FIRMS distributes detections only, with no swath footprints or cloud masks. An unmatched
cell-overpass therefore has three possible meanings that the data cannot fully separate:
the other sensor did not look, it looked but clouds blocked it, or it looked and detected
nothing (fire below its detection limit, or fire changed).

FireSync uses an explicit **coverage proxy**. If sensor B produced *any* detection in the
same 1° cell within the window, B imaged that region at that time. The unmatched A overpass
is then counted as **"B viewed area, no detection in cell"**. Otherwise it is **"B coverage
unknown"**. Agreement rates use only proxy-observed cases:

```
conditional co-detection rate (A→B) = co-detected / (co-detected + B viewed area, no detection)
```

"Coverage unknown" is never counted as disagreement, and "no detection" is never presented
as "no fire". The proxy can still include cloud-obscured cells, so the rate is a lower bound
on true detection agreement.

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

| Pair | Grid | Window | Pairs | median abs Δt | co-det A→B | co-det B→A | log-FRP r / ρ | median FRP B/A [95% CI] | Calibration | held-out median abs error (log10): fit vs 1:1 |
|---|---|---|---:|---|---|---|---|---|---|---|
| NOAA-20 ↔ NOAA-21 | 0.05° | ±60 | 76,741 | 45 min | 0.49 | 0.50 | 0.70 / 0.67 | 1.175 [1.165, 1.185] | slope 0.978 [0.972, 0.985] | 0.280 vs 0.288 |
| NOAA-20 ↔ NOAA-21 | **0.1°** | ±60 | 68,494 | 45 min | 0.56 | 0.57 | 0.72 / 0.69 | 1.176 [1.165, 1.185] | slope 0.980 [0.975, 0.986] | 0.278 vs 0.286 |
| NOAA-20 ↔ NOAA-21 | 0.25° | ±60 | 50,711 | 45 min | 0.68 | 0.69 | 0.77 / 0.75 | 1.172 [1.161, 1.186] | slope 0.982 [0.977, 0.989] | 0.281 vs 0.287 |
| Aqua MODIS ↔ NOAA-20 | 0.1° | ±180 | 13,925 | 128 min | 0.62 | 0.26 | 0.46 / 0.43 | 0.98 [0.96, 1.01] | **refused** (not simultaneous) | — |
| Aqua MODIS ↔ NOAA-21 | 0.1° | ±180 | 13,718 | 131 min | 0.60 | 0.26 | 0.46 / 0.42 | 1.16 [1.13, 1.20] | **refused** (not simultaneous) | — |
| MODIS ↔ NOAA-20 (strict) | 0.1° | ±60 | 282 | 55 min | 0.59 | 0.25 | 0.56 / 0.47 | 0.64 [0.55, 0.77] | fitted, **worse than 1:1** | 0.291 vs 0.184 |
| MODIS ↔ NOAA-21 (strict) | 0.1° | ±60 | 125 | 58 min | 0.58 | 0.29 | 0.55 / 0.51 | 0.78 [0.65, 0.86] | insufficient data | — |

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
4. **Detection asymmetry is expected.** Where VIIRS viewed the area, it co-detects about 60%
   of Aqua MODIS cell-overpasses. MODIS co-detects only about 26% of VIIRS ones. This fits
   VIIRS's smaller pixels detecting smaller and cooler fires that fall below the MODIS limit.
   Do not read it as a VIIRS false-alarm rate.
5. **Agreement grows with coarser cells** (co-detection 0.49 → 0.68 from 0.05° to 0.25°),
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

**Experimental / limitations:**
- The coverage proxy has no cloud information, so co-detection rates are lower bounds.
- Bootstrap CIs resample pairs independently and understate uncertainty from spatial and
  temporal autocorrelation. With only 8 days of data, a block-by-day bootstrap is not yet
  meaningful.
- Pairs are conditional on both sensors detecting, which biases them toward larger fires.
- NRT products only. Standard-processing (SP) data may revise detections and FRP.
- Results describe 6–13 November 2026 and the regions burning then (dominated by the
  African savanna burning season). They are not a global transfer function.
- Planar point-in-polygon and lat/lon boxes do not cross the antimeridian.
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
