"""Historical (standard-processing) FIRMS archive for fixed study regions.

Raw 5-day API responses are kept unchanged (gzipped, SHA-256 in a manifest) under
data/archive/raw/<region>/<source>/ (git-ignored).  They are aggregated to daily counts per
0.25 deg cell (data/archive/<region>/daily_cells.csv.gz) and to monthly region totals
(data/archive/<region>/monthly.csv, tracked).

Only detections of FIRMS `type` 0 ("presumed vegetation fire") are counted toward the
burning calendar; volcanoes (1), other static land sources such as industry (2) and
offshore sources such as gas flares (3) are counted separately and excluded.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = ROOT / "data" / "archive"
RAW_DIR = ARCHIVE_DIR / "raw"
CELL_DEG = 0.25

# Study regions: distinct fire regimes, each a bounding box west,south,east,north (deg).
REGIONS: Dict[str, Dict] = {
    "central_africa": {
        "label": "Central African savanna (north)",
        "bbox": [15.0, 0.0, 32.0, 12.0],
        "regime": "Tropical savanna, dry-season burning Nov-Mar",
    },
    "amazon_arc": {
        "label": "Southern Amazon & Cerrado",
        "bbox": [-62.0, -16.0, -44.0, -4.0],
        "regime": "Deforestation and agricultural fire, Jul-Oct",
    },
    "se_asia": {
        "label": "Mainland Southeast Asia",
        "bbox": [94.0, 12.0, 108.0, 24.0],
        "regime": "Agricultural and forest burning, Feb-Apr",
    },
    "n_australia": {
        "label": "Northern Australia savanna",
        "bbox": [124.0, -20.0, 146.0, -11.0],
        "regime": "Savanna burning, May-Nov",
    },
    "siberia": {
        "label": "Eastern Siberia (boreal)",
        "bbox": [110.0, 50.0, 135.0, 64.0],
        "regime": "Boreal forest fire, Apr-Sep",
    },
    "nile_delta": {
        "label": "Nile Delta, Egypt",
        "bbox": [29.0, 29.5, 33.0, 32.0],
        "regime": "Agricultural residue burning (rice straw), Sep-Nov",
    },
}

SOURCES = {"MODIS_SP": "modis", "VIIRS_SNPP_SP": "viirs_snpp", "VIIRS_NOAA20_SP": "viirs_noaa20"}


def region_dir(region: str) -> Path:
    return ARCHIVE_DIR / region


def manifest_path(region: str) -> Path:
    return region_dir(region) / "manifest.jsonl"


def read_manifest(region: str) -> List[dict]:
    path = manifest_path(region)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def aggregate(region: str) -> Dict[str, int]:
    """Rebuild daily cell counts and monthly totals for one region from its raw chunks."""
    entries = [e for e in read_manifest(region) if e.get("status") == "ok"]
    frames = []
    totals = {"rows": 0, "type0": 0}
    for entry in entries:
        raw = RAW_DIR / region / entry["source"] / entry["file"]
        payload = gzip.decompress(raw.read_bytes())
        import hashlib

        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"{raw}: SHA-256 mismatch with manifest")
        if not entry["rows"]:
            continue
        frame = pd.read_csv(io.BytesIO(payload), dtype={"acq_time": str, "satellite": str, "daynight": str})
        if len(frame) != entry["rows"]:
            raise ValueError(f"{raw}: row count mismatch with manifest")
        frame["source"] = entry["source"]
        frames.append(frame[["latitude", "longitude", "acq_date", "satellite", "daynight", "frp", "type", "source"]])
        totals["rows"] += len(frame)
        totals["type0"] += int((frame["type"] == 0).sum())
    out = region_dir(region)
    if not frames:
        return totals
    data = pd.concat(frames, ignore_index=True)
    data["row"] = np.floor((data["latitude"] + 90) / CELL_DEG).astype(int)
    data["col"] = np.floor((data["longitude"] + 180) / CELL_DEG).astype(int)
    data["is_fire"] = (data["type"] == 0).astype(int)
    data["frp_fire"] = np.where(data["is_fire"] == 1, data["frp"], 0.0)
    daily = (
        data.groupby(["source", "satellite", "acq_date", "row", "col"], sort=True)
        .agg(detections=("is_fire", "size"), fire=("is_fire", "sum"), frp_fire=("frp_fire", "sum"))
        .reset_index()
    )
    daily["frp_fire"] = daily["frp_fire"].round(2)
    with gzip.open(out / "daily_cells.csv.gz", "wt", newline="") as handle:
        daily.to_csv(handle, index=False)
    data["month"] = data["acq_date"].str.slice(0, 7)
    monthly = (
        data.groupby(["source", "satellite", "month"], sort=True)
        .agg(detections=("is_fire", "size"), fire=("is_fire", "sum"), frp_fire=("frp_fire", "sum"))
        .reset_index()
    )
    monthly["frp_fire"] = monthly["frp_fire"].round(1)
    monthly.to_csv(out / "monthly.csv", index=False)
    return totals


def coverage(region: str) -> Dict[str, Dict]:
    """Per source: first/last date fetched, chunks ok/failed, days covered."""
    result: Dict[str, Dict] = {}
    for entry in read_manifest(region):
        info = result.setdefault(entry["source"], {"ok": 0, "failed": 0, "days": 0, "first": None, "last": None})
        if entry.get("status") == "ok":
            info["ok"] += 1
            info["days"] += entry["days"]
            info["first"] = min(filter(None, [info["first"], entry["start"]]))
            info["last"] = max(filter(None, [info["last"], entry["end"]]))
        else:
            info["failed"] += 1
    return result


def load_monthly(region: str) -> pd.DataFrame:
    path = region_dir(region) / "monthly.csv"
    if not path.is_file():
        return pd.DataFrame(columns=["source", "satellite", "month", "detections", "fire", "frp_fire"])
    return pd.read_csv(path, dtype={"satellite": str})


def write_csv_rows(path: Path, rows: List[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
