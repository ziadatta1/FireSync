import hashlib
import os
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import firms
from app.main import DATASETS, app

client = TestClient(app)
TEST_KEY = "unit-test-map-key-secret"
REAL_REQUEST_FIRMS_CSV = firms.request_firms_csv
CSV_PAYLOAD = (
    b"latitude,longitude,bright_ti4,acq_date,satellite,instrument,confidence,frp\n"
    b"12.5,-45.25,312.4,2025-01-02,N,VIIRS,h,4.2\n"
)
TWO_ROW_IMPORT = (
    b"latitude,longitude,bright_ti4,acq_date,satellite,instrument,confidence,frp\n"
    b"12.5,-45.25,312.4,2025-01-02,N20,VIIRS,h,4.2\n"
    b"13.5,-46.25,318.1,2025-01-03,N20,VIIRS,n,6.1\n"
)


def create_test_import(imports_dir, *, sensor="viirs", source="VIIRS_NOAA20_NRT", payload=TWO_ROW_IMPORT):
    rows = payload.decode("utf-8").splitlines()
    metadata = {
        "classification": firms.VERIFIED_STATUS,
        "provider": "NASA FIRMS",
        "sensor": sensor,
        "source": source,
        "source_url": firms.FIRMS_PUBLIC_URL,
        "api_endpoint": firms.FIRMS_API_BASE,
        "retrieved_at": "2026-11-13T10:00:00Z",
        "query": {"area": "world", "day_range": 1, "date": None},
        "http_status": 200,
        "record_count": max(0, len(rows) - 1),
        "columns": rows[0].split(","),
        "evidence": "Validated NASA FIRMS import fixture.",
    }
    return firms.save_import(payload, metadata)


def use_mock_response(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def request_with_mock(**kwargs):
        return REAL_REQUEST_FIRMS_CSV(**kwargs, transport=transport)

    monkeypatch.setattr(firms, "request_firms_csv", request_with_mock)


@pytest.mark.parametrize(
    ("sensor", "source", "expected_source"),
    [
        ("modis", None, "MODIS_NRT"),
        ("viirs", None, "VIIRS_NOAA21_NRT"),
        ("viirs", "VIIRS_NOAA20_NRT", "VIIRS_NOAA20_NRT"),
    ],
)
def test_import_uses_nasa_source_and_writes_provenance_without_touching_originals(
    tmp_path, monkeypatch, sensor, source, expected_source
):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)
    monkeypatch.setattr(firms, "IMPORTS_DIR", tmp_path / "imports")
    before = {name: hashlib.sha256(path.read_bytes()).digest() for name, path in DATASETS.items()}
    requested_urls = []

    def handler(request):
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=CSV_PAYLOAD, headers={"content-type": "text/csv"})

    use_mock_response(monkeypatch, handler)
    request_body = {"sensor": sensor, "area": "-60,-40,60,40", "day_range": 1, "acquisition_date": "2025-01-02"}
    if source:
        request_body["source"] = source
    response = client.post("/api/firms/import", json=request_body)

    after = {name: hashlib.sha256(path.read_bytes()).digest() for name, path in DATASETS.items()}
    assert response.status_code == 200
    body = response.json()["import"]
    assert body["classification"] == firms.VERIFIED_STATUS
    assert body["provider"] == "NASA FIRMS"
    assert body["source"] == expected_source
    assert body["query"] == {"area": "-60,-40,60,40", "day_range": 1, "date": "2025-01-02"}
    assert body["record_count"] == 1
    assert body["retrieved_at"].endswith("Z")
    assert body["sha256"] == hashlib.sha256(CSV_PAYLOAD).hexdigest()
    assert expected_source in requested_urls[0]
    assert TEST_KEY not in response.text
    assert "file" not in body
    assert str(tmp_path) not in response.text
    assert after == before

    stored_file = tmp_path / "imports" / body["import_id"] / "observations.csv"
    metadata_file = tmp_path / "imports" / body["import_id"] / "metadata.json"
    assert stored_file.read_bytes() == CSV_PAYLOAD
    assert metadata_file.exists()
    listed = client.get("/api/firms/imports")
    assert listed.status_code == 200
    assert listed.json()["imports"][0]["classification"] == firms.VERIFIED_STATUS
    assert "file" not in listed.json()["imports"][0]
    assert str(tmp_path) not in listed.text


def test_import_observations_endpoint_preserves_provenance_filters_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(firms, "IMPORTS_DIR", tmp_path / "imports")
    imported = create_test_import(firms.IMPORTS_DIR)

    first = client.get(
        "/api/firms/imports/{}/observations".format(imported["import_id"]),
        params={"instrument": "VIIRS", "confidence": "h", "limit": 1},
    )
    second = client.get(
        "/api/firms/imports/{}/observations".format(imported["import_id"]),
        params={"offset": 1, "limit": 1},
    )

    assert first.status_code == 200
    body = first.json()
    assert body["data_status"] == firms.VERIFIED_STATUS
    assert body["provenance"]["source"] == "VIIRS_NOAA20_NRT"
    assert body["provenance"]["import_id"] == imported["import_id"]
    assert body["total"] == 1
    assert body["has_more"] is False
    assert body["observations"][0]["latitude"] == 12.5
    assert body["observations"][0]["firms_source"] == "VIIRS_NOAA20_NRT"
    assert body["filters"]["instruments"] == ["VIIRS"]
    assert "file" not in body["provenance"]
    assert str(tmp_path) not in first.text

    assert second.status_code == 200
    assert second.json()["total"] == 2
    assert second.json()["observations"][0]["confidence"] == "n"


def test_empty_import_returns_explicit_empty_message(tmp_path, monkeypatch):
    monkeypatch.setattr(firms, "IMPORTS_DIR", tmp_path / "imports")
    empty_payload = b"latitude,longitude,acq_date,brightness,frp\n"
    imported = create_test_import(
        firms.IMPORTS_DIR,
        sensor="modis",
        source="MODIS_NRT",
        payload=empty_payload,
    )

    response = client.get("/api/firms/imports/{}/observations".format(imported["import_id"]))

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["observations"] == []
    assert response.json()["empty_message"] == "This imported dataset contains no observation rows."


def test_invalid_import_id_is_rejected_without_path_details(tmp_path, monkeypatch):
    monkeypatch.setattr(firms, "IMPORTS_DIR", tmp_path / "imports")

    response = client.get("/api/firms/imports/..%2Fsecret/observations")

    assert response.status_code == 404
    assert str(tmp_path) not in response.text


def test_missing_map_key_is_reported_without_exposing_credentials(monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)

    status = client.get("/api/firms/status")
    response = client.post("/api/firms/import", json={"sensor": "modis"})

    assert status.status_code == 200
    assert status.json()["configured"] is False
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credentials_missing"


def test_status_never_returns_configured_key(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)

    response = client.get("/api/firms/status")

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert TEST_KEY not in response.text


def test_invalid_provider_credentials_are_safely_reported(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)

    def handler(_request):
        return httpx.Response(403, text="Invalid MAP_KEY")

    use_mock_response(monkeypatch, handler)
    response = client.post("/api/firms/import", json={"sensor": "modis"})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert TEST_KEY not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        b"<html><body>Service issue</body></html>",
        b"message,error\nfailed,invalid\n",
        b"latitude,longitude,acq_date\n91,0,2025-01-01\n",
        b"latitude,longitude,acq_date\nnot-a-number,0,2025-01-01\n",
        b"latitude,longitude,acq_date\n1,2,not-a-date\n",
    ],
)
def test_malformed_provider_responses_are_rejected_without_import(tmp_path, monkeypatch, payload):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)
    monkeypatch.setattr(firms, "IMPORTS_DIR", tmp_path / "imports")
    use_mock_response(monkeypatch, lambda _request: httpx.Response(200, content=payload))

    response = client.post("/api/firms/import", json={"sensor": "viirs"})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "malformed_response"
    assert not (tmp_path / "imports").exists()


def test_rate_limits_and_unavailable_services_are_handled(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)
    use_mock_response(
        monkeypatch,
        lambda _request: httpx.Response(429, headers={"Retry-After": "120"}, text="rate limited"),
    )

    limited = client.post("/api/firms/import", json={"sensor": "modis"})

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "120"
    assert limited.json()["detail"]["code"] == "rate_limited"

    use_mock_response(monkeypatch, lambda _request: httpx.Response(503, text="unavailable"))
    unavailable = client.post("/api/firms/import", json={"sensor": "modis"})
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "provider_unavailable"

    use_mock_response(monkeypatch, lambda _request: httpx.Response(200, text="Rate limit exceeded"))
    rate_limited_body = client.post("/api/firms/import", json={"sensor": "modis"})
    assert rate_limited_body.status_code == 429
    assert rate_limited_body.json()["detail"]["code"] == "rate_limited"


def test_source_sensor_and_area_are_validated_before_network_call(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(200, content=CSV_PAYLOAD)

    use_mock_response(monkeypatch, handler)
    wrong_source = client.post("/api/firms/import", json={"sensor": "modis", "source": "VIIRS_NOAA20_NRT"})
    bad_area = client.post("/api/firms/import", json={"sensor": "modis", "area": "-200,0,200,90"})
    too_many_days = client.post("/api/firms/import", json={"sensor": "viirs", "day_range": 6})

    assert wrong_source.status_code == 422
    assert bad_area.status_code == 422
    assert too_many_days.status_code == 422
    assert called is False


def test_suomi_npp_is_historical_only_and_requires_an_explicit_date(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", TEST_KEY)
    called = False

    def handler(_request):
        nonlocal called
        called = True
        return httpx.Response(200, content=CSV_PAYLOAD)

    use_mock_response(monkeypatch, handler)
    response = client.post(
        "/api/firms/import",
        json={"sensor": "viirs", "source": "VIIRS_SNPP_NRT", "area": "world", "day_range": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "historical_date_required"
    assert called is False


def test_viirs_sources_and_default_are_current_noaa_products():
    assert firms.DEFAULT_SOURCE["viirs"] == "VIIRS_NOAA21_NRT"
    assert "VIIRS_NOAA20_NRT" in firms.SOURCES_BY_SENSOR["viirs"]
    assert "VIIRS_NOAA21_NRT" in firms.SOURCES_BY_SENSOR["viirs"]
    assert firms.HISTORICAL_ONLY_SOURCES == {"VIIRS_SNPP_NRT", "VIIRS_SNPP_SP"}


@pytest.mark.skipif(not os.environ.get("FIRMS_MAP_KEY", "").strip(), reason="FIRMS_MAP_KEY is not configured")
def test_live_firms_connectivity_if_credentials_are_configured():
    """Make one real API request only when an operator has configured a MAP_KEY."""
    key = firms.get_map_key()
    payload, count, columns = firms.request_firms_csv(
        sensor="modis",
        source="MODIS_NRT",
        area="world",
        day_range=1,
        acquisition_date=date.today(),
        api_key=key,
    )

    assert payload
    assert count >= 0
    assert {"latitude", "longitude", "acq_date"}.issubset({column.casefold() for column in columns})
