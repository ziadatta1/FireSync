"""Register user-supplied NASA FIRMS global 7-day CSV downloads as FireSync datasets.

Usage (from the project root):
    backend/.venv/bin/python backend/scripts/register_global.py \
        --modis ~/Downloads/MODIS_C6_1_Global_7d.csv \
        --noaa20 ~/Downloads/J1_VIIRS_C2_Global_7d.csv \
        --noaa21 ~/Downloads/J2_VIIRS_C2_Global_7d.csv

Each source file is copied byte-for-byte (never modified) into
data/global/<dataset>/observations.csv and a metadata.json provenance sidecar
is written next to it.  The script refuses to overwrite an existing registration
whose SHA-256 differs, so source observations are never silently replaced.

Provenance cross-check: rows from FIRMS API imports already stored under
data/imports (retrieved directly from the FIRMS API with the backend MAP_KEY)
are looked up in the downloaded file.  An exact row match is independent
evidence that the download is genuine FIRMS output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GLOBAL_DIR = ROOT / "data" / "global"
IMPORTS_DIR = ROOT / "data" / "imports"

# FIRMS file naming: MODIS_C6_1_Global_7d / J1_VIIRS_C2_Global_7d (NOAA-20 = JPSS-1)
# / J2_VIIRS_C2_Global_7d (NOAA-21 = JPSS-2).  Satellite codes are verified per row.
SPEC = {
    "modis": {
        "label": "MODIS Collection 6.1 NRT (Terra + Aqua)",
        "firms_source": "MODIS_NRT",
        "expected_satellites": {"T", "A"},
        "expected_file": "MODIS_C6_1_Global_7d.csv",
    },
    "noaa20": {
        "label": "VIIRS 375 m Collection 2 NRT (NOAA-20 / JPSS-1)",
        "firms_source": "VIIRS_NOAA20_NRT",
        "expected_satellites": {"N20"},
        "expected_file": "J1_VIIRS_C2_Global_7d.csv",
    },
    "noaa21": {
        "label": "VIIRS 375 m Collection 2 NRT (NOAA-21 / JPSS-2)",
        "firms_source": "VIIRS_NOAA21_NRT",
        "expected_satellites": {"N21"},
        "expected_file": "J2_VIIRS_C2_Global_7d.csv",
    },
}
REQUIRED = {"latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp", "daynight", "version"}
KEY_COLS = ("latitude", "longitude", "acq_date", "acq_time")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def norm_key(row: dict) -> tuple:
    return (
        f"{float(row['latitude']):.5f}",
        f"{float(row['longitude']):.5f}",
        row["acq_date"],
        str(int(row["acq_time"])).zfill(4),
    )


def granule(row: dict) -> tuple:
    return (row["acq_date"], str(int(row["acq_time"])).zfill(4))


def profile(path: Path, expected_sats: set) -> tuple[dict, set, set]:
    dates: Counter = Counter()
    sats: Counter = Counter()
    versions: Counter = Counter()
    daynight: Counter = Counter()
    missing: Counter = Counter()
    keys: set = set()
    granules: set = set()
    seen_rows: set = set()
    duplicates = 0
    invalid = 0
    rows = 0
    bounds = [90.0, -90.0, 180.0, -180.0]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if not REQUIRED.issubset(columns):
            raise SystemExit(f"{path.name}: missing required FIRMS columns {sorted(REQUIRED - set(columns))}")
        for row in reader:
            rows += 1
            for column, value in row.items():
                if value is None or value == "":
                    missing[column] += 1
            raw = tuple(row.get(column) for column in columns)
            if raw in seen_rows:
                duplicates += 1
            seen_rows.add(raw)
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
                datetime.strptime(row["acq_date"], "%Y-%m-%d")
                float(row["frp"])
            except (TypeError, ValueError):
                invalid += 1
                continue
            bounds = [min(bounds[0], lat), max(bounds[1], lat), min(bounds[2], lon), max(bounds[3], lon)]
            dates[row["acq_date"]] += 1
            sats[row["satellite"]] += 1
            versions[row["version"]] += 1
            daynight[row["daynight"]] += 1
            keys.add(norm_key(row))
            granules.add(granule(row))
    unexpected = set(sats) - expected_sats
    if unexpected:
        raise SystemExit(f"{path.name}: unexpected satellite codes {sorted(unexpected)}; refusing to register")
    return (
        {
            "row_count": rows,
            "columns": columns,
            "acquisition_dates": dict(sorted(dates.items())),
            "date_range": {"start": min(dates), "end": max(dates)},
            "satellite_codes": dict(sats),
            "versions": dict(versions),
            "daynight": dict(daynight),
            "missing_values": dict(missing),
            "duplicate_rows": duplicates,
            "invalid_rows": invalid,
            "bounds": {"south": bounds[0], "north": bounds[1], "west": bounds[2], "east": bounds[3]},
        },
        keys,
        granules,
    )


def cross_check(firms_source: str, keys: set, granules: set) -> list[dict]:
    results = []
    if not IMPORTS_DIR.exists():
        return results
    for folder in sorted(IMPORTS_DIR.iterdir()):
        meta_path = folder / "metadata.json"
        data_path = folder / "observations.csv"
        if not meta_path.is_file() or not data_path.is_file():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("source") != firms_source or not meta.get("record_count"):
            continue
        total = matched = in_shared_granules = matched_in_shared = 0
        with data_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                total += 1
                hit = norm_key(row) in keys
                matched += hit
                if granule(row) in granules:
                    in_shared_granules += 1
                    matched_in_shared += hit
        results.append(
            {
                "import_id": meta["import_id"],
                "retrieved_at": meta["retrieved_at"],
                "query": meta.get("query"),
                "api_rows": total,
                "rows_found_in_download": matched,
                "match_fraction": round(matched / total, 6) if total else None,
                "api_rows_in_granules_present_in_download": in_shared_granules,
                "matched_within_shared_granules": matched_in_shared,
                "shared_granule_match_fraction": round(matched_in_shared / in_shared_granules, 6) if in_shared_granules else None,
                "explanation": (
                    "Rows not found belong to acquisition granules (acq_date+acq_time) absent from the download, "
                    "i.e. NRT granules that arrived after one snapshot or the other was taken."
                    if in_shared_granules == matched_in_shared and matched < total else None
                ),
                "match_key": list(KEY_COLS),
            }
        )
    return results


def register(dataset: str, source_path: Path) -> dict:
    spec = SPEC[dataset]
    source_path = source_path.expanduser().resolve()
    digest = sha256(source_path)
    target_dir = GLOBAL_DIR / dataset
    target = target_dir / "observations.csv"
    meta_path = target_dir / "metadata.json"
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
        if existing.get("sha256") != digest:
            raise SystemExit(
                f"{dataset}: a different file is already registered (sha256 {existing.get('sha256')}). "
                "Refusing to overwrite source observations; move the old registration aside first."
            )
    stats, keys, granules = profile(source_path, spec["expected_satellites"])
    target_dir.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source_path, target)
    if sha256(target) != digest:
        raise SystemExit(f"{dataset}: copy verification failed")
    checks = cross_check(spec["firms_source"], keys, granules)
    verified = any(c["api_rows_in_granules_present_in_download"] >= 100 and c["shared_granule_match_fraction"] == 1.0 for c in checks)
    metadata = {
        "dataset": dataset,
        "label": spec["label"],
        "provider": "NASA FIRMS",
        "firms_source": spec["firms_source"],
        "original_filename": source_path.name,
        "filename_matches_firms_convention": source_path.name == spec["expected_file"],
        "acquisition": "User-supplied download of the FIRMS global 7-day active fire CSV (not fetched by the FireSync importer).",
        "classification": "NASA FIRMS DOWNLOAD (CROSS-CHECKED)" if verified else "NASA FIRMS DOWNLOAD (USER-SUPPLIED)",
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sha256": digest,
        "size_bytes": target.stat().st_size,
        **stats,
        "api_cross_checks": checks,
        "notes": [
            "NRT (near real-time) product: detections may be revised in the later standard (SP) product.",
            "Rows are satellite detections of thermal anomalies, not unique fire incidents.",
            "The final acquisition date is a partial day (file snapshot taken during that UTC day).",
        ],
    }
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in SPEC:
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    chosen = {name: getattr(args, name) for name in SPEC if getattr(args, name)}
    if not chosen:
        parser.error("provide at least one dataset file")
    for name, path in chosen.items():
        meta = register(name, path)
        checks = ", ".join(f"{c['rows_found_in_download']}/{c['api_rows']}" for c in meta["api_cross_checks"]) or "none available"
        print(
            f"{name}: {meta['row_count']} rows, {meta['date_range']['start']}..{meta['date_range']['end']}, "
            f"sats {meta['satellite_codes']}, dup {meta['duplicate_rows']}, invalid {meta['invalid_rows']}, "
            f"API cross-check {checks} -> {meta['classification']}"
        )


if __name__ == "__main__":
    sys.exit(main())
