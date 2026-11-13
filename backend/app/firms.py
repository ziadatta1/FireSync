"""NASA FIRMS API access and immutable import storage."""

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

FIRMS_API_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
FIRMS_PUBLIC_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/"
FIRMS_MAP_KEY_ENV = "FIRMS_MAP_KEY"
IMPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "imports"

SOURCES_BY_SENSOR = {
    "modis": ("MODIS_NRT", "MODIS_SP"),
    "viirs": (
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA20_SP",
        "VIIRS_NOAA21_NRT",
        "VIIRS_NOAA21_SP",
        # Retained for explicitly dated historical retrievals only.
        "VIIRS_SNPP_NRT",
        "VIIRS_SNPP_SP",
    ),
}
HISTORICAL_ONLY_SOURCES = frozenset({"VIIRS_SNPP_NRT", "VIIRS_SNPP_SP"})
DEFAULT_SOURCE = {"modis": "MODIS_NRT", "viirs": "VIIRS_NOAA21_NRT"}
VERIFIED_STATUS = "VERIFIED NASA FIRMS DATA"
UNVERIFIED_STATUS = "UNVERIFIED LOCAL DATA"
DEMONSTRATION_STATUS = "DEMONSTRATION DATA"
PROVENANCE_CLASSES = {VERIFIED_STATUS, UNVERIFIED_STATUS, DEMONSTRATION_STATUS}
IMPORT_ID_PATTERN = re.compile(r"(?:modis|viirs)_[A-Za-z0-9_-]+_\d{8}T\d{6}Z_[0-9a-f]{8}\Z")


class FirmsFailure(Exception):
    """Safe provider error; messages never contain the request URL or API key."""

    def __init__(self, status_code: int, code: str, message: str, retry_after: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after


def get_map_key() -> str:
    key = os.environ.get(FIRMS_MAP_KEY_ENV, "").strip()
    normalized = key.casefold()
    if not key or normalized in {"your_map_key", "replace_me", "changeme", "demo", "none"}:
        raise FirmsFailure(503, "credentials_missing", "FIRMS_MAP_KEY is not configured on the backend.")
    return key


def validate_area(area: str) -> str:
    value = area.strip()
    if value.casefold() == "world":
        return "world"
    parts = value.split(",")
    if len(parts) != 4:
        raise FirmsFailure(422, "invalid_area", "Area must be 'world' or west,south,east,north coordinates.")
    try:
        west, south, east, north = (float(part.strip()) for part in parts)
    except ValueError as exc:
        raise FirmsFailure(422, "invalid_area", "Area coordinates must be numeric.") from exc
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise FirmsFailure(422, "invalid_area", "Area longitudes must be between -180 and 180.")
    if not (-90 <= south <= 90 and -90 <= north <= 90 and south <= north):
        raise FirmsFailure(422, "invalid_area", "Area latitudes must satisfy -90 <= south <= north <= 90.")
    if west > east:
        raise FirmsFailure(422, "invalid_area", "west must be less than or equal to east.")
    return ",".join(part.strip() for part in parts)


def _validate_csv(payload: bytes) -> tuple[int, list[str]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FirmsFailure(502, "malformed_response", "FIRMS returned data that is not valid UTF-8 CSV.") from exc

    lowered = text[:2000].casefold()
    if any(message in lowered for message in ("invalid map_key", "invalid map key", "map_key is invalid", "invalid api key")):
        raise FirmsFailure(401, "invalid_credentials", "NASA FIRMS rejected the configured MAP_KEY.")
    if any(message in lowered for message in ("too many requests", "rate limit", "transaction limit", "exceeded the maximum number of requests")):
        raise FirmsFailure(429, "rate_limited", "NASA FIRMS rate limit was reached; retry later.")
    if text.lstrip().startswith(("<html", "<!doctype html")):
        raise FirmsFailure(502, "malformed_response", "FIRMS returned an HTML page instead of CSV data.")

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        columns = reader.fieldnames or []
        normalized = {column.strip().casefold() for column in columns if column}
        required = {"latitude", "longitude", "acq_date"}
        if not required.issubset(normalized):
            raise FirmsFailure(502, "malformed_response", "FIRMS response is missing required observation columns.")

        latitude_column = next(column for column in columns if column.strip().casefold() == "latitude")
        longitude_column = next(column for column in columns if column.strip().casefold() == "longitude")
        date_column = next(column for column in columns if column.strip().casefold() == "acq_date")
        count = 0
        for row in reader:
            if None in row:
                raise FirmsFailure(502, "malformed_response", "FIRMS returned a row with an unexpected number of fields.")
            try:
                latitude = float(row[latitude_column])
                longitude = float(row[longitude_column])
            except (TypeError, ValueError, KeyError) as exc:
                raise FirmsFailure(502, "malformed_response", "FIRMS returned invalid coordinates.") from exc
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise FirmsFailure(502, "malformed_response", "FIRMS returned coordinates outside valid ranges.")
            try:
                date.fromisoformat(row[date_column])
            except (TypeError, ValueError, KeyError) as exc:
                raise FirmsFailure(502, "malformed_response", "FIRMS returned an invalid acquisition date.") from exc
            count += 1
    except csv.Error as exc:
        raise FirmsFailure(502, "malformed_response", "FIRMS response could not be parsed as CSV.") from exc
    return count, columns


def parse_observations_csv(payload: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a previously validated import into source rows."""
    _, columns = _validate_csv(payload)
    text = payload.decode("utf-8-sig")
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text, newline="")):
        row["latitude"] = float(row["latitude"])
        row["longitude"] = float(row["longitude"])
        rows.append(row)
    return rows, columns


def request_firms_csv(
    *,
    sensor: str,
    source: str,
    area: str,
    day_range: int,
    acquisition_date: Optional[date],
    api_key: str,
    transport: Optional[httpx.BaseTransport] = None,
) -> tuple[bytes, int, list[str]]:
    """Fetch and validate source CSV from FIRMS without exposing credentials."""
    if sensor not in SOURCES_BY_SENSOR or source not in SOURCES_BY_SENSOR[sensor]:
        raise FirmsFailure(422, "invalid_source", "The selected FIRMS source does not match the requested sensor.")
    if not 1 <= day_range <= 5:
        raise FirmsFailure(422, "invalid_day_range", "FIRMS day_range must be between 1 and 5.")
    if source in HISTORICAL_ONLY_SOURCES and acquisition_date is None:
        raise FirmsFailure(
            422,
            "historical_date_required",
            "Suomi NPP is no longer a live source; provide an explicit date for historical retrieval.",
        )

    safe_key = quote(api_key, safe="")
    safe_area = quote(area, safe=",-")
    url = f"{FIRMS_API_BASE}/{safe_key}/{source}/{safe_area}/{day_range}"
    if acquisition_date:
        url += f"/{acquisition_date.isoformat()}"

    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "text/csv"},
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise FirmsFailure(504, "provider_timeout", "NASA FIRMS did not respond before the request timed out.") from exc
    except httpx.RequestError as exc:
        raise FirmsFailure(503, "provider_unavailable", "NASA FIRMS is currently unavailable.") from exc

    if response.status_code in (401, 403):
        raise FirmsFailure(401, "invalid_credentials", "NASA FIRMS rejected the configured MAP_KEY.")
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise FirmsFailure(429, "rate_limited", "NASA FIRMS rate limit was reached; retry later.", retry_after)
    if response.status_code >= 500:
        raise FirmsFailure(503, "provider_unavailable", "NASA FIRMS returned a service error.")
    if response.status_code != 200:
        raise FirmsFailure(502, "provider_error", "NASA FIRMS returned an unexpected response status.")

    record_count, columns = _validate_csv(response.content)
    return response.content, record_count, columns


def save_import(payload: bytes, metadata: dict[str, Any]) -> dict[str, Any]:
    """Persist an import and its provenance sidecar as one new directory."""
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    retrieved = datetime.fromisoformat(metadata["retrieved_at"].replace("Z", "+00:00"))
    timestamp = retrieved.strftime("%Y%m%dT%H%M%SZ")
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", metadata["source"])
    import_id = f"{metadata['sensor']}_{stem}_{timestamp}_{uuid.uuid4().hex[:8]}"
    staging = Path(tempfile.mkdtemp(prefix=".firms-staging-", dir=str(IMPORTS_DIR)))
    target = IMPORTS_DIR / import_id
    try:
        data_name = "observations.csv"
        metadata["import_id"] = import_id
        metadata["file"] = f"data/imports/{import_id}/{data_name}"
        metadata["sha256"] = hashlib.sha256(payload).hexdigest()
        with (staging / data_name).open("xb") as handle:
            handle.write(payload)
        with (staging / "metadata.json").open("x", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)
            handle.write("\n")
        staging.rename(target)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise FirmsFailure(500, "import_storage_failed", "The validated import could not be stored.") from exc
    return metadata


def list_import_metadata() -> list[dict[str, Any]]:
    if not IMPORTS_DIR.exists():
        return []
    imports: list[dict[str, Any]] = []
    for import_dir in sorted(IMPORTS_DIR.iterdir()):
        if not import_dir.is_dir() or import_dir.is_symlink() or not IMPORT_ID_PATTERN.fullmatch(import_dir.name):
            continue
        metadata, _ = load_import(import_dir.name)
        imports.append(metadata)
    return imports


def load_import(import_id: str) -> tuple[dict[str, Any], bytes]:
    """Read one validated import without trusting caller-supplied paths."""
    if not IMPORT_ID_PATTERN.fullmatch(import_id):
        raise FirmsFailure(404, "import_not_found", "Imported dataset not found.")
    try:
        root = IMPORTS_DIR.resolve(strict=True)
        folder = root / import_id
        if folder.is_symlink():
            raise FirmsFailure(404, "import_not_found", "Imported dataset not found.")
        resolved_folder = folder.resolve(strict=True)
        if resolved_folder.parent != root or not resolved_folder.is_dir():
            raise FirmsFailure(404, "import_not_found", "Imported dataset not found.")
        metadata_path = resolved_folder / "metadata.json"
        data_path = resolved_folder / "observations.csv"
        if metadata_path.is_symlink() or data_path.is_symlink():
            raise FirmsFailure(404, "import_not_found", "Imported dataset not found.")
        with metadata_path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
        payload = data_path.read_bytes()
    except FirmsFailure:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise FirmsFailure(404, "import_not_found", "Imported dataset not found.") from exc

    if metadata.get("import_id") != import_id:
        raise FirmsFailure(404, "import_not_found", "Imported dataset not found.")
    if metadata.get("classification") not in PROVENANCE_CLASSES:
        raise FirmsFailure(500, "import_metadata_invalid", "Import provenance metadata is invalid.")
    sensor = metadata.get("sensor")
    source = metadata.get("source")
    if sensor not in SOURCES_BY_SENSOR or source not in SOURCES_BY_SENSOR[sensor]:
        raise FirmsFailure(500, "import_metadata_invalid", "Import source metadata is invalid.")
    if metadata.get("sha256") != hashlib.sha256(payload).hexdigest():
        raise FirmsFailure(500, "import_integrity_error", "Imported data failed its recorded integrity check.")
    return metadata, payload


def public_import_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove internal storage details before returning provenance to clients."""
    return {key: value for key, value in metadata.items() if key != "file"}
