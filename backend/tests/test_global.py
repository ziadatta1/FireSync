"""Tests for the global dataset store, aggregation, comparison and export."""

import csv
import hashlib
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import compare, global_data
from app.main import app

VIIRS_COLS = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time", "satellite",
              "confidence", "version", "bright_ti5", "frp", "daynight"]
MODIS_COLS = ["latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time", "satellite",
              "confidence", "version", "bright_t31", "frp", "daynight"]


def _write(root, name, cols, rows, firms_source):
    folder = root / name
    folder.mkdir(parents=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(cols)
    writer.writerows(rows)
    payload = buffer.getvalue().encode()
    (folder / "observations.csv").write_bytes(payload)
    dates = sorted({r[5] for r in rows})
    meta = {
        "dataset": name, "label": name, "firms_source": firms_source, "classification": "TEST FIXTURE",
        "original_filename": f"{name}.csv", "acquisition": "test", "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload), "row_count": len(rows), "columns": cols,
        "acquisition_dates": {d: sum(r[5] == d for r in rows) for d in dates},
        "date_range": {"start": dates[0], "end": dates[-1]}, "notes": [],
    }
    (folder / "metadata.json").write_text(json.dumps(meta))


def viirs_row(lat, lon, d, t, frp, sat, conf="nominal", dn="D"):
    return [lat, lon, 330, 0.4, 0.4, d, t, sat, conf, "2.0NRT", 290, frp, dn]


def modis_row(lat, lon, d, t, frp, sat="A", conf=70, dn="D"):
    return [lat, lon, 330, 1, 1, d, t, sat, conf, "6.1NRT", 300, frp, dn]


@pytest.fixture()
def fixture_store(tmp_path, monkeypatch):
    rows20, rows21, rows_m = [], [], []
    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
    rng = np.random.default_rng(1)
    # 40 fires per day; NOAA-21 sees each 50 min after NOAA-20 with FRP exactly 2x.
    for d in dates:
        for k in range(40):
            lat, lon = 10 + k * 0.5 + 0.03, 20 + 0.03
            frp = float(round(10 ** rng.uniform(0, 2), 2))
            rows20.append(viirs_row(lat, lon, d, "1200", frp, "N20"))
            rows21.append(viirs_row(lat, lon, d, "1250", round(frp * 2, 2), "N21"))
    # One NOAA-20-only detection where NOAA-21 viewed the same 1-degree cell (coverage proxy = viewed)
    rows20.append(viirs_row(10.73, 20.73, "2026-01-01", "1200", 5.0, "N20"))
    # One NOAA-20-only detection far from any NOAA-21 detection (coverage unknown)
    rows20.append(viirs_row(-40.0, -60.0, "2026-01-01", "1200", 5.0, "N20", conf="low", dn="N"))
    # MODIS three hours away from VIIRS (non-simultaneous)
    for k in range(40):
        rows_m.append(modis_row(10 + k * 0.5 + 0.03, 20.03, "2026-01-01", "1500", 20.0))
    _write(tmp_path, "noaa20", VIIRS_COLS, rows20, "VIIRS_NOAA20_NRT")
    _write(tmp_path, "noaa21", VIIRS_COLS, rows21, "VIIRS_NOAA21_NRT")
    _write(tmp_path, "modis", MODIS_COLS, rows_m, "MODIS_NRT")
    monkeypatch.setattr(global_data, "GLOBAL_DIR", tmp_path)
    global_data._cache.clear()
    from app import main
    main._cached_compare.cache_clear()
    yield tmp_path
    global_data._cache.clear()
    main._cached_compare.cache_clear()


@pytest.fixture()
def client(fixture_store):
    return TestClient(app)


def test_datasets_listing_has_no_paths(client):
    body = client.get("/api/global/datasets").json()
    assert [d["id"] for d in body["datasets"]] == ["modis", "noaa20", "noaa21"]
    text = json.dumps(body)
    assert str(global_data.GLOBAL_DIR) not in text and "observations.csv" not in text


def test_grid_counts_preserve_all_detections(client):
    body = client.get("/api/global/grid", params={"cell": 1.0}).json()
    for name, expected in {"noaa20": 202, "noaa21": 200, "modis": 40}.items():
        cells = body["layers"][name]["cells"]
        assert body["layers"][name]["detections"] == expected
        assert sum(c[2] for c in cells) == expected


def test_grid_rejects_unsupported_cell(client):
    assert client.get("/api/global/grid", params={"cell": 0.3}).status_code == 422


def test_points_truncation_is_explicit_and_keeps_highest_frp(client):
    body = client.get("/api/global/points", params={"south": -90, "west": -180, "north": 90, "east": 180,
                                                    "source": "noaa21", "limit": 10}).json()
    layer = body["layers"]["noaa21"]
    assert layer["total"] == 200 and layer["returned"] == 10 and layer["truncated"] is True
    frp_col = body["columns"].index("frp")
    all_frp = sorted((r[11] for r in _rows("noaa21")), reverse=True)
    assert sorted((r[frp_col] for r in layer["rows"]), reverse=True) == all_frp[:10]


def _rows(name):
    with (global_data.GLOBAL_DIR / name / "observations.csv").open() as handle:
        return [[*r[:11], float(r[11]), r[12]] for r in list(csv.reader(handle))[1:]]


def test_filters_bbox_polygon_confidence_daynight(client):
    s = client.get("/api/global/stats", params={"source": "noaa20", "min_confidence": "nominal"}).json()
    assert s["sources"]["noaa20"]["detections"] == 201
    s = client.get("/api/global/stats", params={"source": "noaa20", "daynight": "N"}).json()
    assert s["sources"]["noaa20"]["detections"] == 1
    s = client.get("/api/global/stats", params={"source": "noaa20", "south": -50, "west": -70, "north": -30, "east": -50}).json()
    assert s["sources"]["noaa20"]["detections"] == 1
    tri = "9,19;9,21;12,20"  # covers latitudes 10.03..~11 near lon 20.03
    s = client.get("/api/global/stats", params={"source": "noaa20", "polygon": tri}).json()
    assert 0 < s["sources"]["noaa20"]["detections"] < 202


def test_stats_daily_counts_and_frp(client):
    s = client.get("/api/global/stats", params={"source": "noaa21"}).json()["sources"]["noaa21"]
    assert s["daily_detections"] == {d: 40 for d in ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]}
    assert s["confidence"] == {"low": 0, "nominal": 200, "high": 0}
    assert s["frp"]["n"] == 200


def test_invalid_parameters_rejected(client):
    assert client.get("/api/global/stats", params={"source": "nope"}).status_code == 422
    assert client.get("/api/global/stats", params={"daynight": "X"}).status_code == 422
    assert client.get("/api/global/stats", params={"polygon": "1,1;2,2"}).status_code == 422
    assert client.get("/api/global/stats", params={"south": 10, "west": 50, "north": 20, "east": 40}).status_code == 422
    assert client.get("/api/global/compare", params={"a": "noaa20", "b": "noaa20"}).status_code == 422


def test_comparison_pairs_and_coverage_proxy(client):
    r = client.get("/api/global/compare", params={"a": "noaa20", "b": "noaa21", "cell": 0.1, "window": 60}).json()
    assert r["a_side"]["cell_overpasses"] == 202
    assert r["a_side"]["co_detected"] == 200
    assert r["a_side"]["other_viewed_area_no_detection"] == 1
    assert r["a_side"]["other_coverage_unknown"] == 1
    assert r["timing"]["median_abs_dt_min"] == 50
    assert r["frp"]["median_ratio_b_over_a"] == pytest.approx(2.0, rel=0.01)
    assert r["frp"]["order_test"]["b_later"]["median_ratio_b_over_a"] == pytest.approx(2.0, rel=0.01)
    assert "b_earlier" not in r["frp"]["order_test"]  # NOAA-21 is always later in the fixture


def test_calibration_recovers_known_relationship_and_validates_out_of_sample(client):
    r = client.get("/api/global/compare", params={"a": "noaa20", "b": "noaa21"}).json()
    cal = r["calibration"]
    assert cal["status"] == "fitted"
    assert cal["slope"] == pytest.approx(1.0, abs=0.01)
    assert cal["intercept"] == pytest.approx(np.log10(2), abs=0.01)
    assert set(cal["train_dates"]).isdisjoint(cal["validation_dates"])
    assert cal["improves_on_identity"] is True


def test_calibration_refused_for_non_simultaneous_pairs(client):
    r = client.get("/api/global/compare", params={"a": "modis", "b": "noaa20", "window": 240}).json()
    assert r["timing"]["median_abs_dt_min"] == 180
    assert r["calibration"]["status"] == "not_supported"
    r = client.get("/api/global/compare", params={"a": "modis", "b": "noaa20", "window": 60}).json()
    assert r["a_side"]["co_detected"] == 0


def test_comparison_restricted_to_common_dates(client):
    r = client.get("/api/global/compare", params={"a": "modis", "b": "noaa20", "window": 240}).json()
    assert r["common_dates"] == ["2026-01-01"]
    assert r["detections"]["b"] == 42  # only NOAA-20 rows on the shared date


def test_export_preserves_source_values_and_provenance(client, fixture_store):
    response = client.get("/api/global/export.csv", params={"source": ["modis", "noaa21"], "date_to": "2026-01-01"})
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 80
    sha = json.loads((fixture_store / "noaa21" / "metadata.json").read_text())["sha256"]
    viirs = [r for r in rows if r["dataset"] == "noaa21"]
    assert all(r["source_sha256"] == sha for r in viirs)
    original = list(csv.DictReader((fixture_store / "noaa21" / "observations.csv").open()))
    first = viirs[0]
    assert original[int(first["source_row"]) - 1]["frp"] == first["frp"]
    assert first["brightness"] == "" and first["bright_ti4"] != ""


def test_integrity_failure_is_reported(client, fixture_store):
    path = fixture_store / "modis" / "observations.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    global_data._cache.clear()
    assert client.get("/api/global/stats", params={"source": "modis"}).status_code == 503


def test_record_lookup_bounds(client):
    assert client.get("/api/global/record/modis/1").json()["fields"]["satellite"] == "A"
    assert client.get("/api/global/record/modis/999").status_code == 404
    assert client.get("/api/global/record/..%2Fetc/1").status_code in (404, 422)


def test_point_in_polygon_square():
    lat = np.array([0.5, 1.5, 0.5])
    lon = np.array([0.5, 0.5, -0.5])
    square = [(0, 0), (0, 1), (1, 1), (1, 0)]
    assert global_data.point_in_polygon(lat, lon, square).tolist() == [True, False, False]


REAL = global_data.GLOBAL_DIR


@pytest.mark.skipif(not (REAL / "noaa21" / "metadata.json").exists(), reason="global datasets not registered")
def test_registered_real_datasets_match_their_provenance():
    global_data._cache.clear()
    for name in global_data.available_sources():
        meta = json.loads((REAL / name / "metadata.json").read_text())
        payload = (REAL / name / "observations.csv").read_bytes()
        assert hashlib.sha256(payload).hexdigest() == meta["sha256"]
        assert payload.count(b"\n") - 1 == meta["row_count"]
        assert meta["duplicate_rows"] == 0 and meta["invalid_rows"] == 0
    global_data._cache.clear()
