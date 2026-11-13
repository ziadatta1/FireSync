"""Satellite swath footprints: did a sensor have a given location in view at a given time?

Footprints are NASA CMR granule polygons (GEODETIC: great-circle edges, counter-clockwise,
convex 4-corner quadrilaterals), fetched by backend/scripts/fetch_swaths.py and stored as
data/global/<dataset>/swaths.json. Containment is tested on the unit sphere, so granules that
cross the antimeridian or approach the poles are handled exactly.

A footprint answers "was this location within view of the sensor", not "was it cloud-free".
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from app import global_data as g

# Angular tolerance (radians) when testing the inside of each great-circle edge.  Swath
# edges are curved scan-line ends approximated by straight (great-circle) segments, which
# NASA notes are imprecise at swath edges.  0 = strict; see swaths_metadata.json validation.
EDGE_TOLERANCE_RAD = 0.0
# FIRMS acq_time is truncated to the minute, so a detection can carry a time slightly
# outside its granule's [start, end].  Matching it to the neighbouring granule is still
# spatially exact (each granule is tested with its own polygon).
TIME_SLACK_MIN = 3.0
# Consecutive granules closer than this are treated as continuous catalogue coverage.
CONTINUITY_GAP_MIN = 0.5


def _unit(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def _minutes(iso: str) -> float:
    stamp = np.datetime64(iso.replace("Z", ""), "s")
    return float(stamp.astype(np.int64)) / 60.0


@dataclass
class Footprints:
    dataset: str
    satellite: np.ndarray  # (G,) str
    start: np.ndarray  # (G,) minutes since epoch
    end: np.ndarray  # (G,) minutes since epoch
    normals: np.ndarray  # (G, 4, 3) inward edge normals (cross(v_i, v_i+1)), unit length
    coverage: Dict[str, np.ndarray]  # satellite -> (K, 2) merged, gap-free catalogue intervals

    def known_at(self, t_min: np.ndarray, satellite: Optional[str] = None, window: float = 0.0) -> np.ndarray:
        """True where the catalogue has granules for the whole of [t - window, t + window].

        NRT catalogues have holes (granules age out, outages).  Inside a hole we cannot say
        whether the sensor looked, so such times must be reported as coverage unknown.
        With several satellites (MODIS Terra + Aqua) any one fully catalogued is enough.
        """
        t = np.asarray(t_min, dtype=float)
        sats = [satellite] if satellite else list(self.coverage)
        ok = np.zeros(t.shape, dtype=bool)
        for sat in sats:
            intervals = self.coverage.get(sat)
            if intervals is None or not len(intervals):
                continue
            i = np.searchsorted(intervals[:, 0], t - window - TIME_SLACK_MIN, side="right") - 1
            valid = i >= 0
            ii = np.clip(i, 0, None)
            ok |= valid & (intervals[ii, 1] >= t + window + TIME_SLACK_MIN)
        return ok

    def contains(self, lat: np.ndarray, lon: np.ndarray, t_min: np.ndarray, window: float, satellite: Optional[str] = None) -> np.ndarray:
        """True where some granule overlapping [t - window, t + window] has (lat, lon) in view."""
        lat = np.asarray(lat, dtype=float)
        t = np.asarray(t_min, dtype=float)
        result = np.zeros(lat.shape, dtype=bool)
        if lat.size == 0:
            return result
        points = _unit(lat, np.asarray(lon, dtype=float))
        order = np.argsort(t, kind="stable")
        t_sorted = t[order]
        granules = np.flatnonzero(self.satellite == satellite) if satellite else np.arange(len(self.start))
        for k in granules:
            lo = np.searchsorted(t_sorted, self.start[k] - window - TIME_SLACK_MIN, side="left")
            hi = np.searchsorted(t_sorted, self.end[k] + window + TIME_SLACK_MIN, side="right")
            if lo >= hi:
                continue
            idx = order[lo:hi]
            idx = idx[~result[idx]]
            if idx.size == 0:
                continue
            # explicit dot products: Accelerate's matmul emits spurious FP warnings on macOS
            nk = self.normals[k]
            p = points[idx]
            inside = np.ones(idx.size, dtype=bool)
            for e in range(nk.shape[0]):
                inside &= p[:, 0] * nk[e, 0] + p[:, 1] * nk[e, 1] + p[:, 2] * nk[e, 2] >= -EDGE_TOLERANCE_RAD
            result[idx[inside]] = True
        return result


_lock = threading.Lock()
_cache: Dict[str, Tuple[str, Optional[Footprints]]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def load(dataset: str) -> Optional[Footprints]:
    """Footprints for a dataset, or None if none have been fetched."""
    path = g.GLOBAL_DIR / dataset / "swaths.json"
    if not path.is_file():
        return None
    stat = path.stat()
    signature = f"{path}:{stat.st_mtime_ns}:{stat.st_size}"
    with _lock:
        cached = _cache.get(dataset)
        if cached and cached[0] == signature:
            return cached[1]
        granules = json.loads(path.read_text())["granules"]
        normals = np.zeros((len(granules), 4, 3))
        for k, granule in enumerate(granules):
            ring = np.array(granule["corners"], dtype=float)
            if len(ring) != 4:
                raise ValueError(f"{dataset}: footprint {granule.get('granule')} has {len(ring)} corners, expected 4")
            v = _unit(ring[:, 0], ring[:, 1])
            n = np.cross(v, np.roll(v, -1, axis=0))
            normals[k] = n / np.linalg.norm(n, axis=1, keepdims=True)
        satellite = np.array([gr["satellite"] for gr in granules])
        start = np.array([_minutes(gr["start"]) for gr in granules])
        end = np.array([_minutes(gr["end"]) for gr in granules])
        coverage = {}
        for sat in np.unique(satellite):
            sel = np.argsort(start[satellite == sat])
            s_, e_ = start[satellite == sat][sel], end[satellite == sat][sel]
            merged = [[s_[0], e_[0]]]
            for a, b in zip(s_[1:], e_[1:]):
                if a <= merged[-1][1] + CONTINUITY_GAP_MIN:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            coverage[str(sat)] = np.array(merged)
        footprints = Footprints(dataset, satellite, start, end, normals, coverage)
        _cache[dataset] = (signature, footprints)
        return footprints
