import pytest
from fastapi.testclient import TestClient

from app.main import DATASETS, DATA_STATUS, app

client = TestClient(app)


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
    ],
)
def test_cors_allows_local_frontend_origins(origin):
    response = client.get("/api/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_preflight_allows_required_post_headers():
    response = client.options(
        "/api/firms/import",
        headers={
            "Origin": "http://127.0.0.1:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()


def test_cors_does_not_allow_unlisted_origins():
    response = client.get("/api/health", headers={"Origin": "http://localhost:5175"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_health_endpoint():
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "FireSync API"}


def test_summary_endpoint_reports_unverified_local_data():
    response = client.get("/api/datasets/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"] == DATA_STATUS
    assert "not claimed to be authenticated NASA observations" in body["provenance_note"]
    assert body["datasets"]["modis"]["status"] == DATA_STATUS
    assert body["datasets"]["viirs"]["status"] == DATA_STATUS
    assert body["datasets"]["modis"]["row_count"] > 0
    assert body["datasets"]["viirs"]["row_count"] > 0


def test_summary_reads_csv_without_modifying_it():
    import hashlib

    before = {name: hashlib.sha256(path.read_bytes()).digest() for name, path in DATASETS.items()}

    response = client.get("/api/datasets/summary")

    after = {name: hashlib.sha256(path.read_bytes()).digest() for name, path in DATASETS.items()}
    assert response.status_code == 200
    assert after == before


def test_observations_are_paginated_and_have_valid_coordinates():
    response = client.get("/api/observations", params={"dataset": "viirs", "limit": 37})

    assert response.status_code == 200
    body = response.json()
    assert body["data_status"] == DATA_STATUS
    assert body["total"] == 74605
    assert body["limit"] == 37
    assert body["has_more"] is True
    assert len(body["observations"]) == 37
    assert all(-90 <= item["latitude"] <= 90 for item in body["observations"])
    assert all(-180 <= item["longitude"] <= 180 for item in body["observations"])
    assert "instrument" in body["observations"][0]


def test_observation_filters_match_source_fields():
    response = client.get(
        "/api/observations",
        params={
            "dataset": "viirs",
            "instrument": "VIIRS",
            "confidence": "h",
            "date_from": "2023-07-12",
            "date_to": "2023-07-12",
            "south": -90,
            "west": -180,
            "north": 90,
            "east": 180,
            "limit": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert all(item["instrument"] == "VIIRS" for item in body["observations"])
    assert all(item["confidence"] == "h" for item in body["observations"])
    assert all(item["acq_date"] == "2023-07-12" for item in body["observations"])


def test_observation_coordinate_validation():
    partial_bounds = client.get("/api/observations", params={"south": 0})
    invalid_latitude = client.get(
        "/api/observations", params={"south": -91, "west": -180, "north": 90, "east": 180}
    )
    reversed_box = client.get(
        "/api/observations", params={"south": -10, "west": 10, "north": 10, "east": -10}
    )

    assert partial_bounds.status_code == 422
    assert invalid_latitude.status_code == 422
    assert reversed_box.status_code == 422


def test_invalid_source_coordinates_are_reported_not_silently_lost(tmp_path, monkeypatch):
    fixture = tmp_path / "coordinates.csv"
    fixture.write_text(
        "latitude,longitude,acq_date,confidence\n"
        "10,20,2024-01-01,n\n"
        "91,20,2024-01-01,h\n"
        "not-a-number,0,2024-01-01,l\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(DATASETS, "viirs", fixture)

    response = client.get("/api/observations", params={"dataset": "viirs"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["invalid_coordinate_count"] == 2
    assert body["observations"][0]["latitude"] == 10


def test_invalid_observation_filters_are_rejected():
    response = client.get("/api/observations", params={"date_from": "2024-02-01", "date_to": "2024-01-01"})
    excessive_limit = client.get("/api/observations", params={"limit": 5001})
    unknown_dataset = client.get("/api/observations", params={"dataset": "sample"})

    assert response.status_code == 422
    assert excessive_limit.status_code == 422
    assert unknown_dataset.status_code == 422
