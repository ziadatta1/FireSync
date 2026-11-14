"""Read-only robustness checks for the historical harmonization (no data is modified).

    backend/.venv/bin/python backend/scripts/robustness_archive.py --out docs/historical_robustness.json

Uses the same fit/predict/metric functions and the same training-only selection rule as
app.harmonize, with alternative inputs:
  * predictor variants: MODIS Terra+Aqua (baseline), Aqua only, Terra only, and all FIRMS
    types (no type==0 filter, applied to both sensors)
  * MODIS processing-version switch located from the raw archive (per month)
  * possible satellite outages: days where one sensor reports nothing in a region while the
    other reports many detections
  * year-by-year VIIRS/MODIS ratios (drift)
  * share of months with very low counts (noise)
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import archive, harmonize as h  # noqa: E402


def variant_table(regions, column, satellites):
    """Region-month table with predictor = MODIS `column` summed over `satellites`; target = VIIRS `column`."""
    frames = []
    for region in regions:
        base = h.region_table(region)
        monthly = archive.load_monthly(region)
        v = monthly[monthly.source == h.TARGET].groupby("month")[column].sum()
        m = monthly[(monthly.source == h.PREDICTOR) & (monthly.satellite.isin(satellites))].groupby("month")[column].sum()
        base = base.copy()
        base["viirs"] = [float(v.get(mo, 0)) if ok else None for mo, ok in zip(base.month, base.complete)]
        base["modis"] = [float(m.get(mo, 0)) if ok else None for mo, ok in zip(base.month, base.complete)]
        frames.append(base)
    return pd.concat(frames, ignore_index=True)


def evaluate(table):
    usable = table[table.complete].copy()
    usable[["viirs", "modis"]] = usable[["viirs", "modis"]].astype(float)
    train, test = usable[usable.year.isin(h.TRAIN_YEARS)], usable[usable.year.isin(h.TEST_YEARS)]
    cv_lam = {}
    for lam in h.LAMBDA_GRID:
        errs = []
        for year in h.TRAIN_YEARS:
            f, held = train[train.year != year], train[train.year == year]
            if len(held) and len(f):
                p = h._predict(h._fit(f, lam), held, "M3")
                errs.extend(np.abs(np.log10(p + 1) - np.log10(held.viirs.to_numpy() + 1)))
        cv_lam[lam] = float(np.median(errs))
    lam = min(cv_lam, key=cv_lam.get)
    cv = {}
    for model in h.MODELS:
        errs = []
        for year in h.TRAIN_YEARS:
            f, held = train[train.year != year], train[train.year == year]
            if len(held) and len(f):
                p = h._predict(h._fit(f, lam), held, model)
                errs.extend(np.abs(np.log10(p + 1) - np.log10(held.viirs.to_numpy() + 1)))
        cv[model] = float(np.median(errs))
    selected = min([m for m in h.MODELS if m != "M0"], key=cv.get)
    params = h._fit(train, lam)
    out = {"selected_model": selected, "lambda": lam, "train_months": len(train), "test_months": len(test), "models": {}}
    for model in h.MODELS:
        pred = h._predict(params, test, model)
        out["models"][model] = {
            "cv_median_abs_log10_error": round(cv[model], 4),
            "test": h._metrics(pred, test),
            "test_by_region": {r: h._metrics(pred[(test.region == r).to_numpy()], test[test.region == r]) for r in sorted(test.region.unique())},
        }
    return out


def version_by_month(region):
    """MODIS processing version per month, read from the raw (unchanged) archive files."""
    counts = {}
    for entry in archive.read_manifest(region):
        if entry.get("status") != "ok" or entry["source"] != "MODIS_SP" or not entry["rows"]:
            continue
        raw = archive.RAW_DIR / region / "MODIS_SP" / entry["file"]
        df = pd.read_csv(io.BytesIO(gzip.decompress(raw.read_bytes())), usecols=["acq_date", "version"], dtype=str)
        for (month, version), n in df.groupby([df.acq_date.str[:7], "version"]).size().items():
            counts.setdefault(month, {})[version] = counts.get(month, {}).get(version, 0) + int(n)
    return dict(sorted(counts.items()))


def outages(region):
    path = archive.region_dir(region) / "daily_cells.csv.gz"
    daily = pd.read_csv(path)
    tot = daily.groupby(["source", "acq_date"])["fire"].sum().unstack(0).fillna(0)
    days = pd.date_range("2012-01-20", "2025-12-31").strftime("%Y-%m-%d")
    tot = tot.reindex(days, fill_value=0)
    v, m = tot.get(h.TARGET, 0 * tot.iloc[:, 0]), tot.get(h.PREDICTOR, 0 * tot.iloc[:, 0])
    thr_v, thr_m = max(20, np.percentile(v[v > 0], 75)) if (v > 0).any() else 20, max(5, np.percentile(m[m > 0], 75)) if (m > 0).any() else 5
    viirs_zero = tot.index[(v == 0) & (m >= thr_m)].tolist()
    modis_zero = tot.index[(m == 0) & (v >= thr_v)].tolist()
    return {
        "rule": f"VIIRS 0 while MODIS >= {thr_m:.0f}, or MODIS 0 while VIIRS >= {thr_v:.0f} (75th percentile of nonzero days)",
        "viirs_zero_modis_active_days": len(viirs_zero), "viirs_zero_examples": viirs_zero[:12],
        "modis_zero_viirs_active_days": len(modis_zero), "modis_zero_examples": modis_zero[:12],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    args = parser.parse_args()
    regions = [r for r in archive.REGIONS if (archive.region_dir(r) / "monthly.csv").is_file()]
    report = {"regions": regions, "variants": {}, "per_region": {}}
    variants = {
        "baseline_type0_terra_aqua": ("fire", ["Terra", "Aqua"]),
        "type0_aqua_only": ("fire", ["Aqua"]),
        "type0_terra_only": ("fire", ["Terra"]),
        "all_types_terra_aqua": ("detections", ["Terra", "Aqua"]),
    }
    for name, (column, sats) in variants.items():
        report["variants"][name] = evaluate(variant_table(regions, column, sats))
        # each region alone (region-specific models only see their own data anyway; pooled M1 differs)
        report["variants"][name]["single_region_runs"] = {r: evaluate(variant_table([r], column, sats))["models"] for r in regions} if len(regions) > 1 else None
    for region in regions:
        monthly = archive.load_monthly(region)
        table = h.region_table(region)
        ok = table[table.complete]
        yearly = monthly.assign(year=monthly.month.str[:4].astype(int)).groupby(["year", "source", "satellite"])[["fire", "detections"]].sum().reset_index()
        piv = yearly.pivot_table(index="year", columns="satellite", values="fire", aggfunc="sum").fillna(0)
        ratio = pd.DataFrame({
            "viirs_over_modis": (piv.get("N", 0) / (piv.get("Aqua", 0) + piv.get("Terra", 0)).clip(lower=1)).round(3),
            "viirs_over_aqua": (piv.get("N", 0) / piv.get("Aqua", 0).clip(lower=1)).round(3),
            "viirs_over_terra": (piv.get("N", 0) / piv.get("Terra", 0).clip(lower=1)).round(3),
            "aqua_share_of_modis": (piv.get("Aqua", 0) / (piv.get("Aqua", 0) + piv.get("Terra", 0)).clip(lower=1)).round(3),
        })
        type_share = monthly.groupby("source")[["fire", "detections"]].sum()
        versions = version_by_month(region)
        switch = [m for m, v in versions.items() if len(v) > 1 or (m > min(versions) and set(v) != set(versions[min(versions)]))]
        report["per_region"][region] = {
            "complete_months": int(table.complete.sum()), "incomplete_months": int((~table.complete).sum()),
            "test_months_with_viirs_below_10": int((ok[ok.year.isin(h.TEST_YEARS)].viirs < 10).sum()),
            "test_months": int(ok.year.isin(h.TEST_YEARS).sum()),
            "type0_share_by_source": {s: round(float(r.fire / max(r.detections, 1)), 4) for s, r in type_share.iterrows()},
            "yearly_ratios": ratio.reset_index().to_dict(orient="records"),
            "modis_versions_first_month": {k: versions[k] for k in list(versions)[:1]},
            "modis_version_months_changed": {m: versions[m] for m in switch[:6]},
            "modis_versions_last_month": {k: versions[k] for k in list(versions)[-1:]},
            "possible_outages": outages(region),
        }
    text = json.dumps(report, indent=1, default=float)
    print(text if len(text) < 20000 else text[:20000] + "\n...")
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
