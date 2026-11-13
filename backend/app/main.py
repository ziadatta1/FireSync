"""Read-only API for the FireSync application foundation."""

import csv
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import firms

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASETS = {
    "modis": PROJECT_ROOT / "modis.csv",
    "viirs": PROJECT_ROOT / "viirs.csv",
}
DATA_STATUS = "UNVERIFIED LOCAL DATA"
PAGE_SIZE_DEFAULT = 2000
PAGE_SIZE_MAX = 5000

app = FastAPI(
    title="FireSync API",
    description=(
        "Local CSV files have unverified provenance and are never represented as "
        "authenticated NASA observations. New NASA FIRMS imports are stored separately "
        "with retrieval and provenance metadata."
    ),
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)


def summarize_csv(path: Path) -> dict[str, Any]:
    """Return a small structural summary without changing the source CSV."""
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            row_count = 0
            dates: list[str] = []
            for row in reader:
                row_count += 1
                acquired = row.get("acq_date", "").strip()
                if acquired:
                    dates.append(acquired)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read {path.name}") from exc

    valid_dates: list[date] = []
    for value in dates:
        try:
            valid_dates.append(date.fromisoformat(value))
        except ValueError:
            continue

    return {
        "status": DATA_STATUS,
        "row_count": row_count,
        "columns": columns,
        "acquisition_date_range": {
            "start": min(valid_dates).isoformat() if valid_dates else None,
            "end": max(valid_dates).isoformat() if valid_dates else None,
        },
    }


@lru_cache(maxsize=4)
def _read_observations(path_text: str, modified_ns: int, file_size: int) -> tuple[list[dict[str, Any]], int]:
    """Cache read-only CSV rows until the file's filesystem signature changes."""
    del modified_ns, file_size  # Used as cache-key components.
    path = Path(path_text)
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows: list[dict[str, Any]] = []
            invalid_coordinates = 0
            for row in reader:
                try:
                    latitude = float(row["latitude"])
                    longitude = float(row["longitude"])
                    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                        raise ValueError("coordinate out of range")
                except (KeyError, TypeError, ValueError):
                    invalid_coordinates += 1
                    continue
                row["latitude"] = latitude
                row["longitude"] = longitude
                rows.append(row)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read {path.name}") from exc
    return rows, invalid_coordinates


def _dataset_rows(name: str) -> tuple[list[dict[str, Any]], list[str], int]:
    path = DATASETS[name]
    try:
        stat = path.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read {path.name}") from exc
    rows, invalid_coordinates = _read_observations(str(path), stat.st_mtime_ns, stat.st_size)
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            columns = csv.DictReader(handle).fieldnames or []
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read {path.name}") from exc
    return rows, columns, invalid_coordinates


def _filter_metadata() -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    for name in DATASETS:
        rows, columns, invalid_coordinates = _dataset_rows(name)
        dates = sorted({row.get("acq_date", "") for row in rows if row.get("acq_date")})
        datasets[name] = {
            "columns": columns,
            "dates": dates,
            "satellites": sorted({row["satellite"] for row in rows if row.get("satellite")}),
            "instruments": sorted({row["instrument"] for row in rows if row.get("instrument")}),
            "confidence_values": sorted({row["confidence"] for row in rows if row.get("confidence")}),
            "invalid_coordinate_count": invalid_coordinates,
        }
    return datasets


@app.get("/api/health")
def health() -> dict[str, str]:
    """Report that the API process is responding."""
    return {"status": "ok", "service": "FireSync API"}


@app.get("/api/datasets/summary")
def dataset_summary() -> dict[str, Any]:
    """Summarize the two local CSV files; no source file is written."""
    return {
        "data_status": DATA_STATUS,
        "provenance_note": (
            "File origin has not been verified. These are not claimed to be "
            "authenticated NASA observations."
        ),
        "datasets": {
            name: {"name": name.upper(), "file": path.name, **summarize_csv(path)}
            for name, path in DATASETS.items()
        },
    }


@app.get("/api/observations/filters")
def observation_filter_options() -> dict[str, Any]:
    """List filter values that actually occur in the local source files."""
    return {"data_status": DATA_STATUS, "datasets": _filter_metadata()}


@app.get("/api/observations")
def observations(
    dataset: Optional[list[str]] = Query(default=None),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    satellite: Optional[str] = None,
    instrument: Optional[str] = None,
    confidence: Optional[str] = None,
    south: Optional[float] = None,
    west: Optional[float] = None,
    north: Optional[float] = None,
    east: Optional[float] = None,
    limit: int = Query(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return a filterable, paginated read-only view of source observations."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")

    bounds = (south, west, north, east)
    if any(value is not None for value in bounds):
        if any(value is None for value in bounds):
            raise HTTPException(status_code=422, detail="Provide south, west, north, and east together")
        if not (-90 <= south <= 90 and -90 <= north <= 90 and south <= north):
            raise HTTPException(status_code=422, detail="Latitude bounds must satisfy -90 <= south <= north <= 90")
        if not (-180 <= west <= 180 and -180 <= east <= 180):
            raise HTTPException(status_code=422, detail="Longitude bounds must be between -180 and 180")
        if west > east:
            raise HTTPException(status_code=422, detail="west must be less than or equal to east; dateline-crossing bounds are not supported")

    selected = list(dict.fromkeys(value.lower() for value in dataset)) if dataset else list(DATASETS)
    unknown = sorted(set(selected) - set(DATASETS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown dataset: {', '.join(unknown)}")

    matches: list[dict[str, Any]] = []
    invalid_coordinate_count = 0
    source_row_counts: dict[str, int] = {}
    for name in selected:
        rows, _, invalid = _dataset_rows(name)
        invalid_coordinate_count += invalid
        source_row_counts[name] = len(rows) + invalid
        for source_row, row in enumerate(rows, start=1):
            acquired = row.get("acq_date", "")
            if date_from and acquired < date_from.isoformat():
                continue
            if date_to and acquired > date_to.isoformat():
                continue
            if satellite and row.get("satellite", "").casefold() != satellite.casefold():
                continue
            if instrument and row.get("instrument", "").casefold() != instrument.casefold():
                continue
            if confidence and row.get("confidence", "").casefold() != confidence.casefold():
                continue
            latitude = row["latitude"]
            longitude = row["longitude"]
            if south is not None and not (south <= latitude <= north and west <= longitude <= east):
                continue
            observation = {key: value for key, value in row.items()}
            observation["dataset"] = name
            observation["source_row"] = source_row
            matches.append(observation)

    page = matches[offset : offset + limit]
    return {
        "data_status": DATA_STATUS,
        "provenance_note": "Source provenance is unverified; these are not claimed to be authenticated NASA observations.",
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(matches),
        "source_row_counts": source_row_counts,
        "invalid_coordinate_count": invalid_coordinate_count,
        "observations": page,
    }


class FirmsImportRequest(BaseModel):
    sensor: Literal["modis", "viirs"]
    source: Optional[str] = None
    area: str = "world"
    day_range: int = Field(default=1, ge=1, le=5)
    acquisition_date: Optional[date] = None


def _firms_http_error(error: firms.FirmsFailure) -> HTTPException:
    headers = None
    if error.retry_after and error.retry_after.isdigit():
        headers = {"Retry-After": str(min(int(error.retry_after), 86400))}
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
        headers=headers,
    )


@app.get("/api/firms/status")
def firms_status() -> dict[str, Any]:
    """Report key configuration without disclosing the credential or testing it."""
    try:
        firms.get_map_key()
        configured = True
    except firms.FirmsFailure:
        configured = False
    return {
        "configured": configured,
        "credential_source": "backend environment variable FIRMS_MAP_KEY",
        "message": "The key value is never returned by this endpoint.",
    }


@app.get("/api/firms/imports")
def firms_imports() -> dict[str, Any]:
    """List provenance records for versioned FIRMS imports."""
    try:
        imports = firms.list_import_metadata()
    except firms.FirmsFailure as exc:
        raise _firms_http_error(exc) from exc
    return {"imports": [firms.public_import_metadata(item) for item in imports]}


@app.post("/api/firms/import")
def import_firms_data(request: FirmsImportRequest) -> dict[str, Any]:
    """Fetch one FIRMS area CSV and persist it separately from local originals."""
    try:
        api_key = firms.get_map_key()
        area = firms.validate_area(request.area)
        source = request.source or firms.DEFAULT_SOURCE[request.sensor]
        if source not in firms.SOURCES_BY_SENSOR[request.sensor]:
            raise firms.FirmsFailure(
                422,
                "invalid_source",
                "The selected FIRMS source does not match the requested sensor.",
            )

        payload, record_count, columns = firms.request_firms_csv(
            sensor=request.sensor,
            source=source,
            area=area,
            day_range=request.day_range,
            acquisition_date=request.acquisition_date,
            api_key=api_key,
        )
        retrieved_utc = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        metadata = {
            "classification": firms.VERIFIED_STATUS,
            "provider": "NASA FIRMS",
            "sensor": request.sensor,
            "source": source,
            "source_url": firms.FIRMS_PUBLIC_URL,
            "api_endpoint": firms.FIRMS_API_BASE,
            "retrieved_at": retrieved_utc,
            "query": {
                "area": area,
                "day_range": request.day_range,
                "date": request.acquisition_date.isoformat() if request.acquisition_date else None,
            },
            "http_status": 200,
            "record_count": record_count,
            "columns": columns,
            "evidence": "Successful HTTPS response from NASA FIRMS; CSV schema and coordinates validated.",
        }
        stored = firms.save_import(payload, metadata)
        return {"import": firms.public_import_metadata(stored)}
    except firms.FirmsFailure as exc:
        raise _firms_http_error(exc) from exc


@app.get("/api/firms/imports/{import_id}/observations")
def imported_observations(
    import_id: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    satellite: Optional[str] = None,
    instrument: Optional[str] = None,
    confidence: Optional[str] = None,
    south: Optional[float] = None,
    west: Optional[float] = None,
    north: Optional[float] = None,
    east: Optional[float] = None,
    limit: int = Query(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Read one validated FIRMS import without exposing its storage path."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    bounds = (south, west, north, east)
    if any(value is not None for value in bounds):
        if any(value is None for value in bounds):
            raise HTTPException(status_code=422, detail="Provide south, west, north, and east together")
        if not (-90 <= south <= 90 and -90 <= north <= 90 and south <= north):
            raise HTTPException(status_code=422, detail="Latitude bounds must satisfy -90 <= south <= north <= 90")
        if not (-180 <= west <= 180 and -180 <= east <= 180 and west <= east):
            raise HTTPException(status_code=422, detail="Longitude bounds must satisfy -180 <= west <= east <= 180")

    try:
        metadata, payload = firms.load_import(import_id)
        rows, columns = firms.parse_observations_csv(payload)
    except firms.FirmsFailure as exc:
        raise _firms_http_error(exc) from exc
    if len(rows) != metadata.get("record_count"):
        raise HTTPException(status_code=500, detail="Imported dataset failed its recorded row-count check.")

    dates = sorted({row["acq_date"] for row in rows if row.get("acq_date")})
    satellites = sorted({row["satellite"] for row in rows if row.get("satellite")})
    instruments = sorted({row["instrument"] for row in rows if row.get("instrument")})
    confidence_values = sorted({row["confidence"] for row in rows if row.get("confidence")})
    matches: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows, start=1):
        acquired = row.get("acq_date", "")
        if date_from and acquired < date_from.isoformat():
            continue
        if date_to and acquired > date_to.isoformat():
            continue
        if satellite and row.get("satellite", "").casefold() != satellite.casefold():
            continue
        if instrument and row.get("instrument", "").casefold() != instrument.casefold():
            continue
        if confidence and row.get("confidence", "").casefold() != confidence.casefold():
            continue
        if south is not None and not (south <= row["latitude"] <= north and west <= row["longitude"] <= east):
            continue
        matches.append(
            {
                **row,
                "dataset": metadata["sensor"],
                "source_row": source_row,
                "import_id": import_id,
                "firms_source": metadata["source"],
                "provenance_status": metadata["classification"],
            }
        )

    page = matches[offset : offset + limit]
    record_count = metadata["record_count"]
    return {
        "data_status": metadata["classification"],
        "provenance": firms.public_import_metadata(metadata),
        "filters": {
            "columns": columns,
            "dates": dates,
            "satellites": satellites,
            "instruments": instruments,
            "confidence_values": confidence_values,
            "invalid_coordinate_count": 0,
        },
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(matches),
        "empty_message": "This imported dataset contains no observation rows." if record_count == 0 else None,
        "observations": page,
    }
