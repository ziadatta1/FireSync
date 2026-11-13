"""Columnar access to registered NASA FIRMS global datasets.

Each dataset (MODIS, VIIRS NOAA-20, VIIRS NOAA-21) is kept separate.  Source
CSV files are opened read-only; everything returned by this module is either a
source row or an aggregation that is explicitly labelled as such.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
GLOBAL_DIR = ROOT / "data" / "global"
SOURCE_ORDER = ("modis", "noaa20", "noaa21")
SOURCE_LABELS = {"modis": "MODIS", "noaa20": "VIIRS NOAA-20", "noaa21": "VIIRS NOAA-21"}
SATELLITE_NAMES = {"T": "Terra", "A": "Aqua", "N20": "NOAA-20", "N21": "NOAA-21", "N": "Suomi NPP"}
# Nominal nadir pixel size (km).  Used for documentation only, never to rescale values.
NOMINAL_PIXEL_KM = {"modis": 1.0, "noaa20": 0.375, "noaa21": 0.375}
CONF_CLASSES = ("low", "nominal", "high")
ALLOWED_CELLS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0)


class DataUnavailable(Exception):
    pass


@dataclass
class Dataset:
    name: str
    metadata: Dict[str, Any]
    frame: pd.DataFrame  # original columns as strings (for export) + typed arrays below
    lat: np.ndarray
    lon: np.ndarray
    frp: np.ndarray
    day: np.ndarray  # int days since 1970-01-01
    tmin: np.ndarray  # minutes since epoch (UTC)
    conf_class: np.ndarray  # 0 low, 1 nominal, 2 high
    is_day: np.ndarray
    satellite: np.ndarray  # str codes
    dates: List[str] = field(default_factory=list)


_lock = threading.Lock()
_cache: Dict[str, Tuple[str, Dataset]] = {}


def _conf_class(series: pd.Series, modis: bool) -> np.ndarray:
    if modis:
        # FIRMS MODIS confidence 0-100: <30 low, 30-79 nominal, >=80 high (FIRMS FAQ classes).
        values = pd.to_numeric(series, errors="coerce").to_numpy()
        out = np.where(values >= 80, 2, np.where(values >= 30, 1, 0))
        return out.astype(np.int8)
    mapping = {"l": 0, "low": 0, "n": 1, "nominal": 1, "h": 2, "high": 2}
    return series.str.strip().str.lower().map(mapping).fillna(-1).to_numpy().astype(np.int8)


def available_sources() -> List[str]:
    return [name for name in SOURCE_ORDER if (GLOBAL_DIR / name / "metadata.json").is_file()]


def load(name: str) -> Dataset:
    if name not in SOURCE_ORDER:
        raise DataUnavailable(f"Unknown dataset: {name}")
    folder = GLOBAL_DIR / name
    meta_path = folder / "metadata.json"
    csv_path = folder / "observations.csv"
    if not meta_path.is_file() or not csv_path.is_file():
        raise DataUnavailable(f"Dataset {name} is not registered")
    stat = csv_path.stat()
    signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    with _lock:
        cached = _cache.get(name)
        if cached and cached[0] == signature:
            return cached[1]
        metadata = json.loads(meta_path.read_text())
        payload = csv_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
            raise DataUnavailable(f"Dataset {name} failed its SHA-256 integrity check")
        frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        if len(frame) != metadata.get("row_count"):
            raise DataUnavailable(f"Dataset {name} failed its row-count check")
        lat = frame["latitude"].astype(float).to_numpy()
        lon = frame["longitude"].astype(float).to_numpy()
        frp = pd.to_numeric(frame["frp"], errors="coerce").to_numpy()
        day = (pd.to_datetime(frame["acq_date"], format="%Y-%m-%d").to_numpy().astype("datetime64[D]").astype(np.int64))
        hhmm = frame["acq_time"].astype(int).to_numpy()
        tmin = day * 1440 + (hhmm // 100) * 60 + (hhmm % 100)
        dataset = Dataset(
            name=name,
            metadata=metadata,
            frame=frame,
            lat=lat,
            lon=lon,
            frp=frp,
            day=day,
            tmin=tmin,
            conf_class=_conf_class(frame["confidence"], name == "modis"),
            is_day=(frame["daynight"].to_numpy() == "D"),
            satellite=frame["satellite"].to_numpy(),
            dates=sorted(frame["acq_date"].unique().tolist()),
        )
        _cache[name] = (signature, dataset)
        return dataset


def day_index(value: str) -> int:
    return int(np.datetime64(value, "D").astype(np.int64))


def day_string(index: int) -> str:
    return str(np.datetime64(int(index), "D"))


@dataclass
class Selection:
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    bbox: Optional[Tuple[float, float, float, float]] = None  # south, west, north, east
    polygon: Optional[List[Tuple[float, float]]] = None  # [(lat, lon), ...]
    daynight: Optional[str] = None  # "D" | "N"
    min_confidence: Optional[str] = None  # low | nominal | high
    satellite: Optional[str] = None  # T / A / N20 / N21


def point_in_polygon(lat: np.ndarray, lon: np.ndarray, polygon: Sequence[Tuple[float, float]]) -> np.ndarray:
    """Even-odd ray casting in lat/lon space (planar approximation, no dateline wrap)."""
    inside = np.zeros(lat.shape, dtype=bool)
    count = len(polygon)
    j = count - 1
    for i in range(count):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        crosses = (yi > lat) != (yj > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = (xj - xi) * (lat - yi) / (yj - yi) + xi
        inside ^= crosses & (lon < x_at)
        j = i
    return inside


def mask(ds: Dataset, sel: Selection) -> np.ndarray:
    keep = np.ones(ds.lat.shape, dtype=bool)
    if sel.date_from:
        keep &= ds.day >= day_index(sel.date_from)
    if sel.date_to:
        keep &= ds.day <= day_index(sel.date_to)
    if sel.bbox:
        south, west, north, east = sel.bbox
        keep &= (ds.lat >= south) & (ds.lat <= north) & (ds.lon >= west) & (ds.lon <= east)
    if sel.polygon:
        lats = [p[0] for p in sel.polygon]
        lons = [p[1] for p in sel.polygon]
        keep &= (ds.lat >= min(lats)) & (ds.lat <= max(lats)) & (ds.lon >= min(lons)) & (ds.lon <= max(lons))
        idx = np.flatnonzero(keep)
        inside = point_in_polygon(ds.lat[idx], ds.lon[idx], sel.polygon)
        keep[idx[~inside]] = False
    if sel.daynight in ("D", "N"):
        keep &= ds.is_day if sel.daynight == "D" else ~ds.is_day
    if sel.min_confidence in CONF_CLASSES:
        keep &= ds.conf_class >= CONF_CLASSES.index(sel.min_confidence)
    if sel.satellite:
        keep &= ds.satellite == sel.satellite
    return keep


def cell_ids(lat: np.ndarray, lon: np.ndarray, cell: float) -> Tuple[np.ndarray, np.ndarray]:
    row = np.floor((lat + 90.0) / cell).astype(np.int64)
    col = np.floor((lon + 180.0) / cell).astype(np.int64)
    ncols = int(round(360.0 / cell))
    col = np.clip(col, 0, ncols - 1)
    return row, col


# --------------------------------------------------------------------------- grid / points


def grid(sources: Iterable[str], sel: Selection, cell: float) -> Dict[str, Any]:
    layers: Dict[str, Any] = {}
    for name in sources:
        ds = load(name)
        keep = mask(ds, sel)
        if not keep.any():
            layers[name] = {"cells": [], "detections": 0}
            continue
        row, col = cell_ids(ds.lat[keep], ds.lon[keep], cell)
        frp = np.nan_to_num(ds.frp[keep])
        key = row * 100000 + col
        uniq, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
        frp_sum = np.bincount(inverse, weights=frp)
        frp_max = np.zeros(len(uniq))
        np.maximum.at(frp_max, inverse, frp)
        lat_c = (uniq // 100000 + 0.5) * cell - 90.0
        lon_c = (uniq % 100000 + 0.5) * cell - 180.0
        cells = np.column_stack([np.round(lat_c, 4), np.round(lon_c, 4), counts, np.round(frp_sum, 2), np.round(frp_max, 2)])
        layers[name] = {"cells": cells.tolist(), "detections": int(keep.sum())}
    return {
        "cell_degrees": cell,
        "columns": ["lat_center", "lon_center", "detections", "frp_sum_mw", "frp_max_mw"],
        "layers": layers,
        "note": "Aggregated detection counts on a regular lat/lon grid; cells are display bins, not sensor footprints.",
    }


POINT_FIELDS = ("latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp", "daynight")


def points(sources: Iterable[str], sel: Selection, limit: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in sources:
        ds = load(name)
        idx = np.flatnonzero(mask(ds, sel))
        total = int(idx.size)
        truncated = total > limit
        if truncated:
            # Deterministic, documented reduction: keep the highest-FRP detections.
            order = np.argsort(-np.nan_to_num(ds.frp[idx]), kind="stable")[:limit]
            idx = np.sort(idx[order])
        sub = ds.frame.iloc[idx]
        rows = [
            [float(r[0]), float(r[1]), r[2], r[3], r[4], r[5], float(r[6]) if r[6] else None, r[7], int(i) + 1]
            for r, i in zip(sub[list(POINT_FIELDS)].itertuples(index=False, name=None), idx)
        ]
        out[name] = {"total": total, "returned": len(rows), "truncated": truncated, "rows": rows}
    return {"columns": list(POINT_FIELDS) + ["source_row"], "layers": out}


def record(name: str, source_row: int) -> Dict[str, Any]:
    ds = load(name)
    if not 1 <= source_row <= len(ds.frame):
        raise DataUnavailable("Row not found")
    row = ds.frame.iloc[source_row - 1].to_dict()
    return {
        "dataset": name,
        "label": SOURCE_LABELS[name],
        "firms_source": ds.metadata["firms_source"],
        "classification": ds.metadata["classification"],
        "source_row": source_row,
        "satellite_name": SATELLITE_NAMES.get(row.get("satellite", ""), row.get("satellite")),
        "confidence_class": CONF_CLASSES[int(ds.conf_class[source_row - 1])] if ds.conf_class[source_row - 1] >= 0 else None,
        "fields": row,
    }


# --------------------------------------------------------------------------- statistics


def _quantiles(values: np.ndarray) -> Optional[Dict[str, float]]:
    values = values[~np.isnan(values)]
    if values.size == 0:
        return None
    q = np.quantile(values, [0.0, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0])
    return {
        "n": int(values.size),
        "min": round(float(q[0]), 2),
        "p25": round(float(q[1]), 2),
        "median": round(float(q[2]), 2),
        "p75": round(float(q[3]), 2),
        "p90": round(float(q[4]), 2),
        "p99": round(float(q[5]), 2),
        "max": round(float(q[6]), 2),
        "mean": round(float(values.mean()), 2),
        "sum": round(float(values.sum()), 1),
    }


FRP_BINS = [0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 1e9]


def stats(sources: Iterable[str], sel: Selection, top_cell: float = 0.25, top_n: int = 10) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    all_dates: set = set()
    for name in sources:
        ds = load(name)
        all_dates.update(ds.dates)
    dates = sorted(all_dates)
    if sel.date_from:
        dates = [d for d in dates if d >= sel.date_from]
    if sel.date_to:
        dates = [d for d in dates if d <= sel.date_to]
    for name in sources:
        ds = load(name)
        keep = mask(ds, sel)
        idx = np.flatnonzero(keep)
        daily_counts = {d: 0 for d in dates if d in ds.dates}
        daily_frp = {d: 0.0 for d in daily_counts}
        if idx.size:
            day_vals, day_counts = np.unique(ds.day[idx], return_counts=True)
            frp_by_day = np.bincount(np.searchsorted(day_vals, ds.day[idx]), weights=np.nan_to_num(ds.frp[idx]))
            for d, c, f in zip(day_vals, day_counts, frp_by_day):
                daily_counts[day_string(d)] = int(c)
                daily_frp[day_string(d)] = round(float(f), 1)
        hours = np.zeros(24, dtype=int)
        if idx.size:
            hours = np.bincount((ds.tmin[idx] % 1440) // 60, minlength=24)
        conf = {c: int((ds.conf_class[idx] == i).sum()) for i, c in enumerate(CONF_CLASSES)}
        sats: Dict[str, int] = {}
        if idx.size:
            s_vals, s_counts = np.unique(ds.satellite[idx], return_counts=True)
            sats = {SATELLITE_NAMES.get(str(s), str(s)): int(c) for s, c in zip(s_vals, s_counts)}
        frp_hist = np.histogram(np.nan_to_num(ds.frp[idx]), bins=FRP_BINS)[0].tolist() if idx.size else [0] * (len(FRP_BINS) - 1)
        top: List[Dict[str, Any]] = []
        if idx.size:
            row, col = cell_ids(ds.lat[idx], ds.lon[idx], top_cell)
            key = row * 100000 + col
            uniq, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
            frp_sum = np.bincount(inverse, weights=np.nan_to_num(ds.frp[idx]))
            for k in np.argsort(-counts, kind="stable")[:top_n]:
                top.append(
                    {
                        "lat_center": round(float((uniq[k] // 100000 + 0.5) * top_cell - 90), 3),
                        "lon_center": round(float((uniq[k] % 100000 + 0.5) * top_cell - 180), 3),
                        "detections": int(counts[k]),
                        "frp_sum_mw": round(float(frp_sum[k]), 1),
                    }
                )
        result[name] = {
            "label": SOURCE_LABELS[name],
            "detections": int(idx.size),
            "dataset_dates": [d for d in dates if d in ds.dates],
            "daily_detections": daily_counts,
            "daily_frp_sum_mw": daily_frp,
            "hourly_utc": hours.tolist(),
            "confidence": conf,
            "daynight": {"day": int(ds.is_day[idx].sum()), "night": int((~ds.is_day[idx]).sum())},
            "satellites": sats,
            "frp": _quantiles(ds.frp[idx]),
            "frp_histogram": {"edges": FRP_BINS[:-1], "counts": frp_hist},
            "top_cells": top,
            "top_cell_degrees": top_cell,
        }
    return {
        "dates": dates,
        "sources": result,
        "note": "Counts are satellite detections (fire pixels), not unique fire incidents.",
    }


def export_rows(sources: Iterable[str], sel: Selection) -> Iterable[str]:
    """Stream matching source rows unchanged, with dataset provenance columns appended."""
    import csv
    import io

    for position, name in enumerate(sources):
        ds = load(name)
        idx = np.flatnonzero(mask(ds, sel))
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        columns = list(ds.frame.columns)
        if position == 0:
            writer.writerow(["dataset", "firms_source", "source_sha256", "source_row", *sorted(set().union(*[set(load(n).frame.columns) for n in sources]), key=_column_order)])
        all_columns = sorted(set().union(*[set(load(n).frame.columns) for n in sources]), key=_column_order)
        values = ds.frame.to_numpy()
        col_index = {c: i for i, c in enumerate(columns)}
        for start in range(0, idx.size, 5000):
            for i in idx[start : start + 5000]:
                row = values[i]
                writer.writerow(
                    [name, ds.metadata["firms_source"], ds.metadata["sha256"], int(i) + 1]
                    + [row[col_index[c]] if c in col_index else "" for c in all_columns]
                )
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
        if not idx.size and position == 0:
            yield buffer.getvalue()


_COLUMN_ORDER = [
    "latitude", "longitude", "brightness", "bright_ti4", "scan", "track", "acq_date", "acq_time",
    "satellite", "confidence", "version", "bright_t31", "bright_ti5", "frp", "daynight",
]


def _column_order(column: str) -> Tuple[int, str]:
    return (_COLUMN_ORDER.index(column) if column in _COLUMN_ORDER else len(_COLUMN_ORDER), column)
