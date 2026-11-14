"""Tests for the historical archive aggregation and year-based harmonization."""

import gzip
import hashlib
import io
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import archive, harmonize
from app.main import _validation_cache, app


def _write_region(root, region, monthly_rows, failed=None):
    folder = root / region
    folder.mkdir(parents=True)
    pd.DataFrame(monthly_rows).to_csv(folder / "monthly.csv", index=False)
    entries = []
    for source in ("MODIS_SP", "VIIRS_SNPP_SP"):
        days = (date(2025, 12, 31) - date(2012, 1, 20)).days + 1
        entries.append({"source": source, "start": "2012-01-20", "end": "2025-12-31", "days": days, "rows": 0,
                        "sha256": "x", "file": "x", "status": "ok"})
    for source, start, days in failed or []:
        entries = [e for e in entries if e["source"] != source]
        # split the full range around the failed chunk
        d0 = date(2012, 1, 20)
        s = date.fromisoformat(start)
        entries.append({"source": source, "start": d0.isoformat(), "end": "", "days": (s - d0).days, "status": "ok"})
        after = date.fromordinal(s.toordinal() + days)
        entries.append({"source": source, "start": after.isoformat(), "end": "2025-12-31",
                        "days": (date(2025, 12, 31) - after).days + 1, "status": "ok"})
        entries.append({"source": source, "start": start, "end": "", "days": days, "status": "failed", "error": "HTTP 503"})
    (folder / "manifest.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _series(ratio_fn, modis_fn):
    rows = []
    for month in harmonize._months("2012-01", "2025-12"):
        y, m = int(month[:4]), int(month[5:])
        mo = modis_fn(y, m)
        rows.append({"source": "MODIS_SP", "satellite": "Aqua", "month": month, "detections": mo, "fire": mo, "frp_fire": 0})
        rows.append({"source": "VIIRS_SNPP_SP", "satellite": "N", "month": month, "detections": 0,
                     "fire": int(round(mo * ratio_fn(y, m))), "frp_fire": 0})
    return rows


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(archive, "RAW_DIR", tmp_path / "raw")
    _validation_cache.clear()
    yield tmp_path
    _validation_cache.clear()


def seasonal_modis(y, m):
    return 100 + 900 * (m in (12, 1, 2)) + 7 * (y - 2012)


def test_constant_ratio_is_recovered_and_beats_no_harmonization(store):
    _write_region(store, "central_africa", _series(lambda y, m: 3.0, seasonal_modis))
    v = harmonize.validate()
    assert v["status"] == "ok"
    assert v["months"]["train"] == 95 and v["months"]["test"] == 72  # Feb 2012 - Dec 2019; 2020-2025
    assert v["parameters"]["region"]["central_africa"]["ratio"] == pytest.approx(3.0, rel=1e-3)
    assert v["models"]["M2"]["test"]["median_abs_log10_error"] < 0.002
    assert v["models"]["M0"]["test"]["median_abs_log10_error"] == pytest.approx(np.log10(3), abs=0.01)


def test_seasonal_ratio_model_wins_when_ratio_is_seasonal(store):
    _write_region(store, "central_africa", _series(lambda y, m: 5.0 if m in (12, 1, 2) else 2.0, seasonal_modis))
    v = harmonize.validate()
    assert v["selected_model"] in ("M3", "M4")
    assert v["models"]["M3"]["test"]["median_abs_log10_error"] < v["models"]["M2"]["test"]["median_abs_log10_error"]


def test_test_years_never_influence_fitted_parameters(store):
    _write_region(store, "central_africa", _series(lambda y, m: 3.0, seasonal_modis))
    base = harmonize.validate()["parameters"]
    _validation_cache.clear()
    (store / "central_africa").rename(store / "old")
    _write_region(store, "central_africa", _series(lambda y, m: 3.0 if y < 2020 else 40.0, seasonal_modis))
    changed = harmonize.validate()
    assert changed["parameters"] == base  # test-year values changed wildly; parameters did not
    assert changed["models"]["M2"]["test"]["median_abs_log10_error"] > 1.0  # and the test exposes it


def test_drift_shows_up_in_per_year_errors(store):
    _write_region(store, "central_africa", _series(lambda y, m: 3.0 * (1.15 ** max(0, y - 2019)), seasonal_modis))
    v = harmonize.validate()
    years = v["models"]["M2"]["test_by_year"]
    assert years[2025]["median_abs_log10_error"] > years[2020]["median_abs_log10_error"]


def test_month_with_failed_chunk_is_excluded_not_zero(store):
    _write_region(store, "central_africa", _series(lambda y, m: 3.0, seasonal_modis), failed=[("MODIS_SP", "2021-03-10", 5)])
    table = harmonize.region_table("central_africa")
    row = table[table.month == "2021-03"].iloc[0]
    assert row["complete"] is False or row["complete"] == False  # noqa: E712
    assert row["viirs"] is None or pd.isna(row["viirs"])
    assert harmonize.validate()["months"]["test"] == 71


def test_aggregate_counts_only_vegetation_fire_and_checks_hashes(store):
    region = "nile_delta"
    folder = store / region
    folder.mkdir(parents=True)
    csv_text = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight,type\n"
        "30.1,31.1,330,0.4,0.4,2015-10-01,1100,N,VIIRS,n,2,290,5.0,D,0\n"
        "30.1,31.1,330,0.4,0.4,2015-10-02,1100,N,VIIRS,n,2,290,9.0,D,2\n"
    ).encode()
    raw = store / "raw" / region / "VIIRS_SNPP_SP"
    raw.mkdir(parents=True)
    (raw / "c.csv.gz").write_bytes(gzip.compress(csv_text))
    entry = {"source": "VIIRS_SNPP_SP", "start": "2015-10-01", "end": "2015-10-05", "days": 5, "rows": 2,
             "sha256": hashlib.sha256(csv_text).hexdigest(), "file": "c.csv.gz", "status": "ok"}
    (folder / "manifest.jsonl").write_text(json.dumps(entry) + "\n")
    totals = archive.aggregate(region)
    assert totals == {"rows": 2, "type0": 1}
    monthly = pd.read_csv(folder / "monthly.csv")
    assert monthly.iloc[0]["detections"] == 2 and monthly.iloc[0]["fire"] == 1 and monthly.iloc[0]["frp_fire"] == 5.0
    entry["sha256"] = "0" * 64
    (folder / "manifest.jsonl").write_text(json.dumps(entry) + "\n")
    with pytest.raises(ValueError):
        archive.aggregate(region)


def test_archive_api(store):
    _write_region(store, "central_africa", _series(lambda y, m: 3.0, seasonal_modis))
    client = TestClient(app)
    regions = client.get("/api/archive/regions").json()["regions"]
    assert {r["id"]: r["available"] for r in regions}["central_africa"] is True
    assert "tmp" not in json.dumps(regions)
    cal = client.get("/api/archive/central_africa/calendar").json()
    first = cal["months"][0]
    assert first["month"] == "2012-02" and first["split"] == "train"
    assert cal["months"][-1]["split"] == "test"
    assert cal["months"][-1]["modis_harmonized"] == pytest.approx(cal["months"][-1]["modis"] * 3, rel=0.01)
    assert client.get("/api/archive/nowhere/calendar").status_code == 404
    assert client.get("/api/archive/nile_delta/calendar").status_code == 404


def test_model_selection_uses_training_years_only(store):
    _write_region(store, "central_africa", _series(lambda y, m: 5.0 if m in (12, 1, 2) else 2.0, seasonal_modis))
    first = harmonize.validate()
    cv = first["selection"]["cv_median_abs_log10_error_by_model"]
    assert first["selected_model"] == min(["M1", "M2", "M3", "M4"], key=lambda m: cv[m])
    _validation_cache.clear()
    (store / "central_africa").rename(store / "old")
    # identical training years, completely different test years (constant ratio 40)
    _write_region(store, "central_africa", _series(lambda y, m: (5.0 if m in (12, 1, 2) else 2.0) if y < 2020 else 40.0, seasonal_modis))
    second = harmonize.validate()
    assert second["selected_model"] == first["selected_model"]
    assert second["selection"] == first["selection"]
    assert second["models"]["M3"]["test"] != first["models"]["M3"]["test"]  # test results do change


def test_validation_with_no_completed_region_does_not_crash(store):
    client = TestClient(app)
    response = client.get("/api/archive/validation")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data" and body["regions"] == []
    assert client.get("/api/archive/regions").status_code == 200


def test_calendar_uses_cv_selected_model(store):
    _write_region(store, "central_africa", _series(lambda y, m: 3.0, seasonal_modis))
    client = TestClient(app)
    val = client.get("/api/archive/validation").json()
    cal = client.get("/api/archive/central_africa/calendar").json()
    assert cal["harmonization_model"] == val["selected_model"]
    assert "best_model_on_test" not in val
