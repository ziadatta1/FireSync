"""Fetch satellite swath footprints (granule outlines) from NASA CMR for each registered dataset.

    backend/.venv/bin/python backend/scripts/fetch_swaths.py            # all registered datasets
    backend/.venv/bin/python backend/scripts/fetch_swaths.py noaa21     # one dataset

Source: NASA Common Metadata Repository (public, no credentials). These are the same
granule bounding polygons FIRMS uses for its "Satellite Swath Outlines" layer: one
GEODETIC 4-corner polygon per 6-min VIIRS / 5-min MODIS Level-2 active-fire granule.

Writes, next to each dataset:
    data/global/<dataset>/swaths.json            granules: satellite, start, end, day/night, corners
    data/global/<dataset>/swaths_metadata.json   provenance + validation

Validation recorded in the sidecar: the fraction of the dataset's own detections that fall
inside a footprint of their own satellite at their own acquisition time. This tests the
geometry, the time alignment, and NASA's caveat that outlines are imprecise at swath edges.
NRT granules age out of CMR after about a week, so footprints may start after the dataset does.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import global_data, swaths  # noqa: E402

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
COLLECTIONS = {
    "modis": [("T", "MOD14", "6.1NRT"), ("A", "MYD14", "6.1NRT")],
    "noaa20": [("N20", "VJ114IMG_NRT", "2")],
    "noaa21": [("N21", "VJ214IMG_NRT", "2")],
}


def fetch(short_name: str, version: str, start: str, end: str) -> list[dict]:
    params = {"short_name": short_name, "version": version, "temporal": f"{start},{end}", "page_size": 2000, "sort_key": "start_date"}
    headers: dict = {}
    entries: list[dict] = []
    with httpx.Client(timeout=60, headers={"Accept": "application/json"}) as client:
        while True:
            response = client.get(CMR_URL, params=params, headers=headers)
            response.raise_for_status()
            batch = response.json()["feed"]["entry"]
            entries.extend(batch)
            after = response.headers.get("cmr-search-after")
            if not batch or not after or len(batch) < params["page_size"]:
                return entries
            headers = {"CMR-Search-After": after}


def corners(entry: dict) -> list[list[float]]:
    polygons = entry.get("polygons") or []
    if len(polygons) != 1:
        raise ValueError(f"{entry.get('title')}: expected one polygon, got {len(polygons)}")
    values = list(map(float, polygons[0][0].split()))
    ring = [[values[i], values[i + 1]] for i in range(0, len(values), 2)]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        raise ValueError(f"{entry.get('title')}: degenerate polygon")
    return ring


def main(names: list[str]) -> None:
    for name in names or global_data.available_sources():
        ds = global_data.load(name)
        start = f"{ds.dates[0]}T00:00:00Z"
        end = f"{ds.dates[-1]}T23:59:59Z"
        granules, collections = [], []
        for satellite, short_name, version in COLLECTIONS[name]:
            entries = fetch(short_name, version, start, end)
            if entries and entries[0].get("coordinate_system") != "GEODETIC":
                raise SystemExit(f"{short_name}: unexpected coordinate system {entries[0].get('coordinate_system')}")
            for entry in entries:
                granules.append({
                    "satellite": satellite,
                    "start": entry["time_start"],
                    "end": entry["time_end"],
                    "day_night": entry.get("day_night_flag"),
                    "granule": entry.get("producer_granule_id") or entry.get("title"),
                    "corners": corners(entry),
                })
            collections.append({
                "satellite": satellite,
                "short_name": short_name,
                "version": version,
                "concept_id": entries[0]["collection_concept_id"] if entries else None,
                "granules": len(entries),
                "first_start": entries[0]["time_start"] if entries else None,
                "last_end": entries[-1]["time_end"] if entries else None,
            })
        granules.sort(key=lambda g: (g["satellite"], g["start"]))
        folder = global_data.GLOBAL_DIR / name
        payload = (json.dumps({"dataset": name, "granules": granules}, separators=(",", ":")) + "\n").encode()
        (folder / "swaths.json").write_bytes(payload)

        # Validation: own detections inside own footprints (needs the file just written).
        swaths.clear_cache()
        fp = swaths.load(name)
        validation = {}
        for satellite in sorted({g["satellite"] for g in granules}):
            keep = ds.satellite == satellite
            known = keep & fp.known_at(ds.tmin, satellite)
            inside = fp.contains(ds.lat[known], ds.lon[known], ds.tmin[known].astype(float), 0, satellite)
            gaps = fp.coverage[satellite]
            validation[satellite] = {
                "catalogue_intervals": int(len(gaps)),
                "catalogued_minutes": round(float((gaps[:, 1] - gaps[:, 0]).sum()), 1),
                "detections": int(keep.sum()),
                "detections_in_catalogued_time": int(known.sum()),
                "detections_in_catalogue_gaps_or_before_first_granule": int((keep & ~known).sum()),
                "inside_own_footprint": int(inside.sum()),
                "inside_fraction": round(float(inside.mean()), 6) if known.any() else None,
            }
        metadata = {
            "dataset": name,
            "provider": "NASA Common Metadata Repository (CMR)",
            "endpoint": CMR_URL,
            "query_temporal": [start, end],
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "collections": collections,
            "granule_count": len(granules),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "geometry": "GEODETIC polygon per granule (great-circle edges), from CMR granule metadata",
            "validation_own_detections_inside_own_footprint": validation,
            "caveats": [
                "Outlines are derived from granule bounding coordinates and are imprecise at swath edges (NASA FIRMS).",
                "A footprint shows the area within view of the sensor, not cloud-free or valid land pixels.",
                "NRT granules age out of CMR, so footprints may not cover the first part of the dataset period.",
            ],
        }
        (folder / "swaths_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        summary = ", ".join(f"{c['satellite']}: {c['granules']} granules from {c['first_start']}" for c in collections)
        checks = ", ".join(f"{s}: {v['inside_fraction']}" for s, v in validation.items())
        print(f"{name}: {summary} | own detections inside own footprint: {checks}")


if __name__ == "__main__":
    main(sys.argv[1:])
