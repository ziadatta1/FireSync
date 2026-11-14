"""Paired spatio-temporal comparison between two FIRMS detection datasets.

Method (documented in docs/METHODOLOGY.md):

1. Each sensor's detections are binned on a common lat/lon grid (default 0.1 deg).
   Within a cell, detections whose times are within EVENT_GAP_MIN of each other
   are merged into one "cell-overpass" (a single satellite look at that cell).
   FRP is additive, so the cell-overpass FRP is the sum of its pixels' FRP.
2. Cell-overpasses of sensor A are matched one-to-one to the nearest-in-time
   cell-overpass of sensor B in the same cell, if |dt| <= window.
3. Unmatched cell-overpasses are classified by whether B had the fire location in
   view within the time window.  When B's swath footprints are available (NASA CMR
   granule polygons, app/swaths.py) this is tested directly: "viewed, no detection",
   "not in view", or "unknown" (catalogue gap).  Without footprints a weaker proxy is
   used: any B detection in the same coarse cell (COARSE_DEG) within the window.
   A footprint shows what was in view, not whether it was cloud-free.
   A-looks whose nearest B look was taken by another A-look of the same cell are
   counted separately and excluded from agreement rates.
4. FRP agreement is measured on matched pairs in log10 space.  A reduced-major-
   axis (RMA) fit, which treats both sensors as error-bearing, is estimated on
   the earlier dates and evaluated on held-out later dates against an identity
   (no-calibration) baseline.  A fit is only reported when the pair is near-
   simultaneous (median |dt| <= CALIBRATION_MAX_MEDIAN_DT_MIN).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app import global_data as g
from app import swaths

EVENT_GAP_MIN = 20
COARSE_DEG = 1.0
CALIBRATION_MAX_MEDIAN_DT_MIN = 60
MIN_PAIRS_FOR_FIT = 30
BOOTSTRAP = 400
SCATTER_MAX = 1500
RNG_SEED = 20261113


def _events(ds: g.Dataset, keep: np.ndarray, cell: float) -> pd.DataFrame:
    idx = np.flatnonzero(keep)
    if idx.size == 0:
        return pd.DataFrame(columns=["cell", "coarse", "t", "frp", "n", "plat", "plon", "day"])
    row, col = g.cell_ids(ds.lat[idx], ds.lon[idx], cell)
    crow, ccol = g.cell_ids(ds.lat[idx], ds.lon[idx], COARSE_DEG)
    frame = pd.DataFrame(
        {
            "cell": row * 100000 + col,
            "coarse": crow * 1000 + ccol,
            "t": ds.tmin[idx],
            "frp": np.nan_to_num(ds.frp[idx]),
            "plat": ds.lat[idx],
            "plon": ds.lon[idx],
        }
    ).sort_values(["cell", "t"], kind="stable")
    new_event = (frame["cell"].diff() != 0) | (frame["t"].diff() > EVENT_GAP_MIN)
    frame["event"] = new_event.cumsum()
    events = frame.groupby("event", sort=False).agg(
        cell=("cell", "first"), coarse=("coarse", "first"), t=("t", "min"), frp=("frp", "sum"), n=("frp", "size"),
        plat=("plat", "mean"), plon=("plon", "mean"),
    )
    events["day"] = events["t"] // 1440
    return events.reset_index(drop=True)


def _match(a: pd.DataFrame, b: pd.DataFrame, window: int) -> pd.DataFrame:
    """One-to-one nearest-time matching of A events to B events within the same cell."""
    a = a.copy()
    a["a_id"] = np.arange(len(a))
    b = b.copy()
    b["b_id"] = np.arange(len(b))
    if a.empty or b.empty:
        a["b_id"] = np.nan
        a["raw_hit"] = False
        return a
    left = a.sort_values("t")[["a_id", "cell", "t"]].astype("int64")
    right = b.sort_values("t")[["b_id", "cell", "t"]].astype("int64").rename(columns={"t": "tb"})
    right["t"] = right["tb"]
    merged = pd.merge_asof(left, right, on="t", by="cell", direction="nearest", tolerance=window)
    merged["adt"] = (merged["tb"] - merged["t"]).abs()
    hits = merged.dropna(subset=["b_id"]).sort_values(["adt", "a_id"], kind="stable")
    hits = hits.drop_duplicates("b_id", keep="first")
    a["b_id"] = a["a_id"].map(hits.set_index("a_id")["b_id"])
    # True when a B look was within the window in this cell before one-to-one resolution;
    # if it is now unmatched, its B look was paired with another A look instead.
    a["raw_hit"] = a["a_id"].isin(merged.loc[merged["b_id"].notna(), "a_id"]).to_numpy()
    return a


def _coverage_proxy(unmatched: pd.DataFrame, other: pd.DataFrame, window: int) -> np.ndarray:
    """True where the other sensor detected anything in the same coarse cell within the window."""
    if unmatched.empty or other.empty:
        return np.zeros(len(unmatched), dtype=bool)
    left = unmatched[["coarse", "t"]].astype("int64").reset_index().sort_values("t")
    right = other[["coarse", "t"]].astype("int64").sort_values("t").copy()
    right["hit"] = 1
    merged = pd.merge_asof(left, right, on="t", by="coarse", direction="nearest", tolerance=window)
    return merged.set_index("index").loc[unmatched.index, "hit"].notna().to_numpy()


def _rma(x: np.ndarray, y: np.ndarray) -> tuple:
    sx, sy = x.std(ddof=1), y.std(ddof=1)
    if sx == 0 or sy == 0:
        return np.nan, np.nan
    r = np.corrcoef(x, y)[0, 1]
    slope = np.sign(r) * sy / sx if sx > 0 else np.nan
    return slope, y.mean() - slope * x.mean()


def _safe(value: float, digits: int = 4) -> Optional[float]:
    value = float(value)
    return round(value, digits) if np.isfinite(value) else None


def _pearson(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None
    return _safe(np.corrcoef(x, y)[0, 1])


def _spearman(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None
    return _safe(pd.Series(x).rank().corr(pd.Series(y).rank()))


def _err(pred: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    resid = pred - y
    return {
        "bias_log10": round(float(resid.mean()), 4),
        "median_abs_error_log10": round(float(np.median(np.abs(resid))), 4),
        "rmse_log10": round(float(np.sqrt(np.mean(resid**2))), 4),
        "median_abs_error_factor": round(float(10 ** np.median(np.abs(resid))), 3),
    }


def compare(
    source_a: str,
    source_b: str,
    sel: g.Selection,
    cell: float = 0.1,
    window: int = 60,
    satellite_a: Optional[str] = None,
    satellite_b: Optional[str] = None,
) -> Dict[str, Any]:
    ds_a, ds_b = g.load(source_a), g.load(source_b)
    common_dates = sorted(set(ds_a.dates) & set(ds_b.dates))
    sel_a = g.Selection(**{**sel.__dict__, "satellite": satellite_a})
    sel_b = g.Selection(**{**sel.__dict__, "satellite": satellite_b})
    # Only compare dates both datasets cover; outside that, absence is "no data", not "no fire".
    if common_dates:
        sel_a.date_from = max(sel.date_from or common_dates[0], common_dates[0])
        sel_a.date_to = min(sel.date_to or common_dates[-1], common_dates[-1])
        sel_b.date_from, sel_b.date_to = sel_a.date_from, sel_a.date_to
    keep_a, keep_b = g.mask(ds_a, sel_a), g.mask(ds_b, sel_b)
    ev_a, ev_b = _events(ds_a, keep_a, cell), _events(ds_b, keep_b, cell)

    matched_a = _match(ev_a, ev_b, window)
    matched_b = _match(ev_b, ev_a, window)  # for B-side coverage accounting only
    a_hit = matched_a["b_id"].notna().to_numpy()

    def side(events: pd.DataFrame, matched: pd.DataFrame, other_events: pd.DataFrame, other_source: str, other_sat: Optional[str]) -> Dict[str, Any]:
        hit = matched["b_id"].notna().to_numpy()
        paired_elsewhere = ~hit & matched["raw_hit"].to_numpy()
        rest = ~hit & ~paired_elsewhere
        unmatched = events[rest]
        footprints = swaths.load(other_source)
        not_viewed = 0
        if footprints is not None:
            t = unmatched["t"].to_numpy(dtype=float)
            viewed = footprints.contains(unmatched["plat"].to_numpy(), unmatched["plon"].to_numpy(), t, window, other_sat)
            known = footprints.known_at(t, other_sat, window)
            viewed_no_detection = int(viewed.sum())
            not_viewed = int((~viewed & known).sum())
            unknown = int((~viewed & ~known).sum())
            method = "swath_footprint"
        else:
            proxy = _coverage_proxy(unmatched, other_events, window)
            viewed_no_detection = int(proxy.sum())
            unknown = int((~proxy).sum())
            method = "coarse_cell_proxy"
        hits = int(hit.sum())
        denom = hits + viewed_no_detection
        return {
            "cell_overpasses": int(len(events)),
            "co_detected": hits,
            "other_viewed_area_no_detection": viewed_no_detection,
            "other_not_in_view": not_viewed,
            "other_coverage_unknown": unknown,
            "other_look_paired_elsewhere": int(paired_elsewhere.sum()),
            "conditional_co_detection_rate": round(hits / denom, 4) if denom else None,
            "coverage_method": method,
        }

    pairs = matched_a[a_hit].copy()
    pairs["b_id"] = pairs["b_id"].astype(int)
    pairs["frp_b"] = ev_b["frp"].to_numpy()[pairs["b_id"]]
    pairs["n_b"] = ev_b["n"].to_numpy()[pairs["b_id"]]
    pairs["t_b"] = ev_b["t"].to_numpy()[pairs["b_id"]]
    pairs["dt"] = pairs["t_b"] - pairs["t"]
    valid = pairs[(pairs["frp"] > 0) & (pairs["frp_b"] > 0)]
    x = np.log10(valid["frp"].to_numpy())
    y = np.log10(valid["frp_b"].to_numpy())
    median_abs_dt = float(np.median(np.abs(pairs["dt"]))) if len(pairs) else None

    frp_block: Dict[str, Any] = {"pairs": int(len(valid))}
    if len(valid) >= 3:
        frp_block.update(
            {
                "pearson_r_log10": _pearson(x, y),
                "spearman_rho": _spearman(x, y),
                "median_ratio_b_over_a": round(float(10 ** np.median(y - x)), 4),
                "median_pixels_per_pair": {"a": float(valid["n"].median()), "b": float(valid["n_b"].median())},
            }
        )
        rng = np.random.default_rng(RNG_SEED)
        boots = [np.median(y[s] - x[s]) for s in (rng.integers(0, len(x), len(x)) for _ in range(BOOTSTRAP))]
        frp_block["median_ratio_95ci"] = [round(float(10 ** np.percentile(boots, 2.5)), 4), round(float(10 ** np.percentile(boots, 97.5)), 4)]
        # Pairs on the same day share weather, fire regime and orbit geometry, so the
        # pair bootstrap above understates uncertainty.  Resampling whole days is the
        # more honest interval (own generator, so the outputs above are unchanged).
        day_ids = np.unique(valid["day"].to_numpy(), return_inverse=True)[1]
        n_days = int(day_ids.max()) + 1
        frp_block["days"] = n_days
        if n_days >= 3:
            by_day = [(y - x)[day_ids == d] for d in range(n_days)]
            rng_day = np.random.default_rng(RNG_SEED + 2)
            boots_day = [np.median(np.concatenate([by_day[d] for d in rng_day.integers(0, n_days, n_days)])) for _ in range(BOOTSTRAP)]
            frp_block["median_ratio_95ci_day_clustered"] = [round(float(10 ** np.percentile(boots_day, 2.5)), 4), round(float(10 ** np.percentile(boots_day, 97.5)), 4)]
        sample = rng.choice(len(x), size=min(SCATTER_MAX, len(x)), replace=False)
        frp_block["scatter_sample_log10"] = np.round(np.column_stack([x[sample], y[sample]]), 3).tolist()

        # Order test: if B/A differs only because fires evolve between looks, the ratio
        # should flip depending on which sensor looked first.  A ratio that is the
        # same in both orders points to a systematic (not temporal) difference between the
        # two observations: calibration, viewing geometry or processing.
        order: Dict[str, Any] = {}
        dts = valid["t_b"].to_numpy() - valid["t"].to_numpy()
        for key, sub in (("b_later", dts > 0), ("b_earlier", dts < 0)):
            if sub.sum() >= 10:
                diffs = (y - x)[sub]
                boot_o = [np.median(diffs[s]) for s in (rng.integers(0, len(diffs), len(diffs)) for _ in range(BOOTSTRAP // 2))]
                order[key] = {
                    "pairs": int(sub.sum()),
                    "median_ratio_b_over_a": round(float(10 ** np.median(diffs)), 4),
                    "ci95": [round(float(10 ** np.percentile(boot_o, 2.5)), 4), round(float(10 ** np.percentile(boot_o, 97.5)), 4)],
                }
        frp_block["order_test"] = order

    calibration: Dict[str, Any] = {"status": "not_computed"}
    days = sorted(valid["day"].unique().tolist()) if len(valid) else []
    if median_abs_dt is None or median_abs_dt > CALIBRATION_MAX_MEDIAN_DT_MIN:
        calibration = {
            "status": "not_supported",
            "reason": (
                f"Median |dt| between paired overpasses is {median_abs_dt:.0f} min (> {CALIBRATION_MAX_MEDIAN_DT_MIN} min). "
                "Fire radiative power changes over hours (diurnal cycle), so these pairs are not simultaneous "
                "observations and cannot support a calibration fit."
                if median_abs_dt is not None
                else "No paired overpasses."
            ),
        }
    elif len(days) < 2:
        calibration = {"status": "insufficient_data", "reason": "Need at least two dates for a held-out validation."}
    else:
        n_train_days = max(1, int(round(len(days) * 0.6)))
        train_days, test_days = days[:n_train_days], days[n_train_days:]
        train = valid["day"].isin(train_days).to_numpy()
        test = ~train
        if train.sum() < MIN_PAIRS_FOR_FIT or test.sum() < MIN_PAIRS_FOR_FIT:
            calibration = {"status": "insufficient_data", "reason": f"Need >= {MIN_PAIRS_FOR_FIT} pairs in both training and validation dates.", "train_pairs": int(train.sum()), "test_pairs": int(test.sum())}
        else:
            slope, intercept = _rma(x[train], y[train])
            rng = np.random.default_rng(RNG_SEED + 1)
            xt, yt = x[train], y[train]
            boot = np.array([_rma(xt[s], yt[s]) for s in (rng.integers(0, len(xt), len(xt)) for _ in range(BOOTSTRAP))])
            pred = intercept + slope * x[test]
            fitted = _err(pred, y[test])
            identity = _err(x[test], y[test])
            improves = fitted["median_abs_error_log10"] < identity["median_abs_error_log10"]
            calibration = {
                "status": "fitted",
                "model": "log10(FRP_B) = intercept + slope * log10(FRP_A)  (reduced major axis, cell-overpass FRP sums)",
                "slope": _safe(slope),
                "intercept": _safe(intercept),
                "slope_95ci": [round(float(np.percentile(boot[:, 0], 2.5)), 4), round(float(np.percentile(boot[:, 0], 97.5)), 4)],
                "intercept_95ci": [round(float(np.percentile(boot[:, 1], 2.5)), 4), round(float(np.percentile(boot[:, 1], 97.5)), 4)],
                "train_dates": [g.day_string(d) for d in train_days],
                "validation_dates": [g.day_string(d) for d in test_days],
                "train_pairs": int(train.sum()),
                "validation_pairs": int(test.sum()),
                "validation_fitted": fitted,
                "validation_identity_baseline": identity,
                "validation_pearson_r_log10": _pearson(x[test], y[test]),
                "improves_on_identity": bool(improves),
                "caveats": [
                    "Pairs exist only where both sensors detected fire, so the fit is conditional on co-detection (selection bias toward larger fires).",
                    "Bootstrap CIs resample pairs independently and understate uncertainty from spatial/temporal autocorrelation.",
                    "Valid only for the sampled region, season, and dates; not a global transfer function.",
                ],
            }

    dt_hist = np.histogram(pairs["dt"], bins=np.arange(-window, window + 1, max(5, window // 12)))[0].tolist() if len(pairs) else []

    # Spatial occupancy agreement over the whole period (no timing constraint).
    occ_a, occ_b = set(ev_a["cell"].unique().tolist()), set(ev_b["cell"].unique().tolist())
    union = occ_a | occ_b
    both = occ_a & occ_b

    return {
        "source_a": source_a,
        "source_b": source_b,
        "labels": {"a": g.SOURCE_LABELS[source_a], "b": g.SOURCE_LABELS[source_b]},
        "satellite_filter": {"a": satellite_a, "b": satellite_b},
        "cell_degrees": cell,
        "window_minutes": window,
        "common_dates": common_dates,
        "compared_dates": [sel_a.date_from, sel_a.date_to],
        "detections": {"a": int(keep_a.sum()), "b": int(keep_b.sum())},
        "a_side": side(ev_a, matched_a, ev_b, source_b, satellite_b),
        "b_side": side(ev_b, matched_b, ev_a, source_a, satellite_a),
        "timing": {
            "pairs": int(len(pairs)),
            "median_abs_dt_min": round(median_abs_dt, 1) if median_abs_dt is not None else None,
            "dt_histogram": {"edges": np.arange(-window, window + 1, max(5, window // 12)).tolist(), "counts": dt_hist},
        },
        "frp": frp_block,
        "calibration": calibration,
        "spatial_occupancy": {
            "cells_a": len(occ_a),
            "cells_b": len(occ_b),
            "cells_both": len(both),
            "jaccard": round(len(both) / len(union), 4) if union else None,
            "note": "Cells with >=1 detection over the compared dates, regardless of overpass timing.",
        },
        "method": {
            "event_gap_minutes": EVENT_GAP_MIN,
            "coverage_proxy_cell_degrees": COARSE_DEG,
            "footprint_time_slack_min": swaths.TIME_SLACK_MIN,
            "footprints_available": {source_a: swaths.load(source_a) is not None, source_b: swaths.load(source_b) is not None},
            "calibration_max_median_dt_min": CALIBRATION_MAX_MEDIAN_DT_MIN,
            "train_fraction_of_dates": 0.6,
            "bootstrap_resamples": BOOTSTRAP,
        },
    }
