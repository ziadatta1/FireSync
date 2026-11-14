"""Year-based harmonization of MODIS to VIIRS (Suomi NPP) monthly fire counts.

Target: monthly count of VIIRS S-NPP "presumed vegetation fire" detections (FIRMS type 0)
per study region.  Predictor: the MODIS (Terra + Aqua) count for the same region and month.
Detections are *not* fires, and the two sensors differ in pixel size and overpass time, so
harmonization here means: estimate the VIIRS-equivalent monthly count from MODIS.

Design (no information from the test years is used for fitting, tuning or model selection):
    train years 2012-2019 (from 2012-02; S-NPP starts 2012-01-20), test years 2020-2025
    hyper-parameters chosen by leave-one-year-out cross-validation inside 2012-2019
    the harmonization model is selected by the same leave-one-year-out CV inside 2012-2019;
    test-period errors are reported for every model but never used to choose one
    a month is used only if every 5-day archive chunk covering it was fetched for both
    sensors (a failed chunk makes the month unknown, never zero)

Models (all fitted per region on training months):
    M0  no harmonization          VIIRS_hat = MODIS
    M1  pooled constant ratio      one ratio for all regions
    M2  regional constant ratio    one ratio per region
    M3  regional seasonal ratio    ratio per region x calendar month, shrunk toward M2
    M4  regional log-log           log(V+1) = a + b log(M+1), ordinary least squares
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from app import archive

TRAIN_YEARS = list(range(2012, 2020))
TEST_YEARS = list(range(2020, 2026))
FIRST_MONTH = "2012-02"
LAST_MONTH = "2025-12"
TARGET, PREDICTOR = "VIIRS_SNPP_SP", "MODIS_SP"
LAMBDA_GRID = [0, 10, 50, 200, 1000, 5000]
MODELS = {
    "M0": "No harmonization (MODIS count as-is)",
    "M1": "Pooled constant ratio",
    "M2": "Regional constant ratio",
    "M3": "Regional seasonal ratio (shrunk)",
    "M4": "Regional log-log regression",
}


def _months(first: str, last: str) -> List[str]:
    out, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def complete_months(region: str) -> Dict[str, set]:
    """Months where every day was fetched successfully, per source."""
    days: Dict[str, set] = {}
    for entry in archive.read_manifest(region):
        if entry.get("status") != "ok":
            continue
        d = date.fromisoformat(entry["start"])
        for i in range(entry["days"]):
            days.setdefault(entry["source"], set()).add((d + timedelta(days=i)).isoformat())
    result = {}
    for source, have in days.items():
        good = set()
        for month in _months(FIRST_MONTH, LAST_MONTH):
            y, m = int(month[:4]), int(month[5:])
            first = date(y, m, 1)
            nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
            if all((first + timedelta(days=i)).isoformat() in have for i in range((nxt - first).days)):
                good.add(month)
        result[source] = good
    return result


def region_table(region: str) -> pd.DataFrame:
    """One row per month: VIIRS and MODIS vegetation-fire counts, with completeness flags."""
    monthly = archive.load_monthly(region)
    complete = complete_months(region)
    months = _months(FIRST_MONTH, LAST_MONTH)
    rows = []
    for month in months:
        both = month in complete.get(TARGET, set()) and month in complete.get(PREDICTOR, set())
        v = monthly[(monthly.source == TARGET) & (monthly.month == month)]["fire"].sum()
        mo = monthly[(monthly.source == PREDICTOR) & (monthly.month == month)]
        rows.append({
            "region": region, "month": month, "year": int(month[:4]), "cal_month": int(month[5:]),
            "viirs": int(v) if both else None,
            "modis": int(mo["fire"].sum()) if both else None,
            "modis_aqua": int(mo[mo.satellite == "Aqua"]["fire"].sum()) if both else None,
            "modis_terra": int(mo[mo.satellite == "Terra"]["fire"].sum()) if both else None,
            "complete": bool(both),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- models

def _fit(train: pd.DataFrame, lam: float) -> Dict:
    params: Dict = {"pooled": train["viirs"].sum() / max(train["modis"].sum(), 1), "lambda": lam, "region": {}}
    for region, g in train.groupby("region"):
        r = g["viirs"].sum() / max(g["modis"].sum(), 1)
        seasonal = {}
        for cm, gm in g.groupby("cal_month"):
            seasonal[int(cm)] = (gm["viirs"].sum() + lam * r) / (gm["modis"].sum() + lam) if (gm["modis"].sum() + lam) > 0 else r
        x, y = np.log10(g["modis"] + 1.0), np.log10(g["viirs"] + 1.0)
        b, a = (np.polyfit(x, y, 1) if x.std() > 0 else (1.0, float((y - x).mean())))
        params["region"][region] = {"ratio": r, "seasonal": seasonal, "a": float(a), "b": float(b)}
    return params


def _predict(params: Dict, data: pd.DataFrame, model: str) -> np.ndarray:
    m = data["modis"].to_numpy(dtype=float)
    if model == "M0":
        return m
    if model == "M1":
        return m * params["pooled"]
    out = np.empty(len(data))
    for i, (region, cm, mv) in enumerate(zip(data["region"], data["cal_month"], m)):
        p = params["region"].get(region)
        if p is None:
            out[i] = mv * params["pooled"]
        elif model == "M2":
            out[i] = mv * p["ratio"]
        elif model == "M3":
            out[i] = mv * p["seasonal"].get(int(cm), p["ratio"])
        else:
            out[i] = 10 ** (p["a"] + p["b"] * np.log10(mv + 1.0)) - 1.0
    return np.clip(out, 0, None)


def _metrics(pred: np.ndarray, data: pd.DataFrame) -> Dict:
    v = data["viirs"].to_numpy(dtype=float)
    err = np.log10(pred + 1) - np.log10(v + 1)
    annual = pd.DataFrame({"y": data["year"], "p": pred, "v": v}).groupby("y").sum()
    ape = (annual["p"] - annual["v"]).abs() / annual["v"].clip(lower=1)
    def spearman(a, b):
        if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
            return None
        return round(float(pd.Series(a).rank().corr(pd.Series(b).rank())), 4)
    return {
        "months": int(len(v)),
        "median_abs_log10_error": round(float(np.median(np.abs(err))), 4),
        "median_abs_error_factor": round(float(10 ** np.median(np.abs(err))), 3),
        "bias_log10": round(float(np.median(err)), 4),
        "spearman_monthly": spearman(pred, v),
        "annual_total_median_abs_pct_error": round(float(np.median(ape) * 100), 1),
    }


def _anomaly_agreement(params: Dict, train: pd.DataFrame, test: pd.DataFrame, model: str) -> Dict:
    """Do harmonized MODIS and observed VIIRS flag the same unusual months?

    Anomaly = log10(count+1) minus that region's training-period mean for the calendar month.
    The VIIRS climatology comes from observed VIIRS, the MODIS one from harmonized MODIS, each
    from training years only.
    """
    def anomalies(frame, values, clim_frame, clim_values):
        clim = pd.DataFrame({"region": clim_frame["region"], "cm": clim_frame["cal_month"], "v": np.log10(clim_values + 1)}) \
            .groupby(["region", "cm"])["v"].mean()
        keys = list(zip(frame["region"], frame["cal_month"]))
        return np.log10(values + 1) - np.array([clim.get(k, np.nan) for k in keys])
    a_v = anomalies(test, test["viirs"].to_numpy(float), train, train["viirs"].to_numpy(float))
    a_m = anomalies(test, _predict(params, test, model), train, _predict(params, train, model))
    ok = ~np.isnan(a_v) & ~np.isnan(a_m)
    a_v, a_m = a_v[ok], a_m[ok]
    big = np.abs(a_v) >= np.log10(1.5)  # months where VIIRS is >=50% above/below normal
    return {
        "months": int(ok.sum()),
        "anomaly_correlation": round(float(np.corrcoef(a_v, a_m)[0, 1]), 4) if ok.sum() > 2 and a_v.std() > 0 and a_m.std() > 0 else None,
        "sign_agreement": round(float((np.sign(a_v) == np.sign(a_m)).mean()), 4) if ok.any() else None,
        "sign_agreement_large_anomalies": round(float((np.sign(a_v[big]) == np.sign(a_m[big])).mean()), 4) if big.any() else None,
        "large_anomaly_months": int(big.sum()),
    }


def validate(regions: Optional[List[str]] = None) -> Dict:
    regions = regions or [r for r in archive.REGIONS if (archive.region_dir(r) / "monthly.csv").is_file()]
    if not regions:
        return {"status": "insufficient_data", "regions": [], "train_months": 0, "test_months": 0,
                "reason": "No study region has a completed, aggregated archive yet."}
    table = pd.concat([region_table(r) for r in regions], ignore_index=True)
    usable = table[table["complete"]].copy()
    usable[["viirs", "modis"]] = usable[["viirs", "modis"]].astype(float)
    train = usable[usable["year"].isin(TRAIN_YEARS)]
    test = usable[usable["year"].isin(TEST_YEARS)]
    if train.empty or test.empty:
        return {"status": "insufficient_data", "regions": regions,
                "train_months": int(len(train)), "test_months": int(len(test))}

    # Choose lambda (M3 shrinkage) by leave-one-year-out CV within the training years only.
    cv = {}
    for lam in LAMBDA_GRID:
        errs = []
        for year in TRAIN_YEARS:
            fit_part, held = train[train.year != year], train[train.year == year]
            if held.empty or fit_part.empty:
                continue
            pred = _predict(_fit(fit_part, lam), held, "M3")
            errs.extend(np.abs(np.log10(pred + 1) - np.log10(held["viirs"].to_numpy(float) + 1)))
        cv[lam] = float(np.median(errs)) if errs else np.inf
    best_lambda = min(cv, key=cv.get)
    params = _fit(train, best_lambda)

    # Model selection: leave-one-year-out CV within the training years only.
    cv_models: Dict[str, float] = {}
    for model in MODELS:
        errs = []
        for year in TRAIN_YEARS:
            fit_part, held = train[train.year != year], train[train.year == year]
            if held.empty or fit_part.empty:
                continue
            pred = _predict(_fit(fit_part, best_lambda), held, model)
            errs.extend(np.abs(np.log10(pred + 1) - np.log10(held["viirs"].to_numpy(float) + 1)))
        cv_models[model] = float(np.median(errs)) if errs else np.inf
    harmonizers = [m for m in MODELS if m != "M0"]
    selected = min(harmonizers, key=lambda m: cv_models[m])

    results = {}
    for model in MODELS:
        pred_test = _predict(params, test, model)
        per_year = {int(y): _metrics(pred_test[(test.year == y).to_numpy()], test[test.year == y]) for y in TEST_YEARS if (test.year == y).any()}
        per_region = {r: _metrics(pred_test[(test.region == r).to_numpy()], test[test.region == r]) for r in regions if (test.region == r).any()}
        results[model] = {
            "label": MODELS[model],
            "test": _metrics(pred_test, test),
            "train_in_sample": _metrics(_predict(params, train, model), train),
            "test_by_year": per_year,
            "test_by_region": per_region,
            "anomaly_agreement_test": _anomaly_agreement(params, train, test, model),
        }
    test_err = {m: results[m]["test"]["median_abs_log10_error"] for m in MODELS}
    lowest_test = min(harmonizers, key=lambda m: test_err[m])
    ratio_by_year = (
        usable.groupby(["region", "year"])[["viirs", "modis"]].sum()
        .assign(ratio=lambda d: (d["viirs"] / d["modis"].clip(lower=1)).round(3))["ratio"]
        .reset_index().to_dict(orient="records")
    )
    return {
        "status": "ok",
        "design": {
            "target": "VIIRS S-NPP monthly presumed-vegetation-fire detections (FIRMS type 0)",
            "predictor": "MODIS Terra+Aqua monthly presumed-vegetation-fire detections",
            "train_years": [TRAIN_YEARS[0], TRAIN_YEARS[-1]],
            "test_years": [TEST_YEARS[0], TEST_YEARS[-1]],
            "first_month": FIRST_MONTH,
            "tuning": "M3 shrinkage chosen by leave-one-year-out CV on training years only",
            "cv_median_abs_log10_error_by_lambda": {str(k): round(v, 4) for k, v in cv.items()},
            "chosen_lambda": best_lambda,
            "month_rule": "month used only if all archive chunks for both sensors were fetched",
        },
        "regions": regions,
        "months": {"train": int(len(train)), "test": int(len(test)), "excluded_incomplete": int((~table["complete"]).sum())},
        "parameters": {
            "pooled_ratio": round(float(params["pooled"]), 4),
            "region": {r: {"ratio": round(p["ratio"], 4), "a": round(p["a"], 4), "b": round(p["b"], 4),
                           "seasonal": {str(k): round(float(v), 4) for k, v in p["seasonal"].items()}}
                       for r, p in params["region"].items()},
        },
        "models": results,
        "selected_model": selected,
        "selection": {
            "method": "lowest median |log10 error| in leave-one-year-out CV over 2012-2019 (training years only)",
            "cv_median_abs_log10_error_by_model": {m: round(v, 4) for m, v in cv_models.items()},
            "no_harmonization_beats_selected_in_training_cv": bool(cv_models["M0"] <= cv_models[selected]),
        },
        "selected_beats_no_harmonization_on_test": bool(test_err[selected] < test_err["M0"]),
        "selected_beats_pooled_ratio_on_test": bool(test_err[selected] < test_err["M1"]) if selected != "M1" else None,
        "lowest_test_error_model_diagnostic_only": lowest_test,
        "ratio_viirs_over_modis_by_year": ratio_by_year,
    }


def calendar(region: str) -> Dict:
    """Monthly series for the calendar UI: observed VIIRS, observed MODIS, harmonized MODIS."""
    table = region_table(region)
    out = {"region": region, "label": archive.REGIONS[region]["label"], "months": []}
    val = validate()
    if val.get("status") != "ok":
        params = None
        model = None
    else:
        usable = pd.concat([region_table(r) for r in val["regions"]], ignore_index=True)
        usable = usable[usable["complete"]].copy()
        usable[["viirs", "modis"]] = usable[["viirs", "modis"]].astype(float)
        params = _fit(usable[usable.year.isin(TRAIN_YEARS)], val["design"]["chosen_lambda"])
        model = val["selected_model"]
    for _, row in table.iterrows():
        harmonized = None
        if params is not None and row["complete"]:
            frame = pd.DataFrame([{"region": region, "cal_month": row["cal_month"], "modis": float(row["modis"])}])
            harmonized = round(float(_predict(params, frame, model)[0]), 1)
        out["months"].append({
            "month": row["month"], "complete": bool(row["complete"]),
            "split": "train" if row["year"] in TRAIN_YEARS else "test",
            "viirs": row["viirs"], "modis": row["modis"],
            "modis_aqua": row["modis_aqua"], "modis_terra": row["modis_terra"],
            "modis_harmonized": harmonized,
        })
    out["harmonization_model"] = model
    return out
