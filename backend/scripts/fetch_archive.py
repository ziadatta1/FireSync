"""Download the FIRMS standard-processing archive for one study region, resumably.

    backend/.venv/bin/python backend/scripts/fetch_archive.py central_africa
    backend/.venv/bin/python backend/scripts/fetch_archive.py central_africa --sources MODIS_SP,VIIRS_SNPP_SP \
        --start 2012-01-20 --end 2025-12-31 --workers 4

Uses the FIRMS Area API (5-day windows) with FIRMS_MAP_KEY from backend/.env.  The key is
never printed, logged or stored.  Every response is validated (app.firms._validate_csv),
saved unchanged (gzipped) with its SHA-256 in data/archive/<region>/manifest.jsonl, and the
run can be interrupted and resumed: chunks already in the manifest are skipped.

The MAP_KEY allows 5,000 transactions per 10 minutes; a 5-day request on a box up to about
40x40 deg costs about 10.  The fetcher checks the key's transaction count and pauses before
the limit.  Afterwards it rebuilds the region's daily-cell and monthly aggregates.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import archive, firms  # noqa: E402

API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
STATUS = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/"
QUOTA_PAUSE_AT = 4300
lock = threading.Lock()


def chunks(start: date, end: date):
    day = start
    while day <= end:
        span = min(5, (end - day).days + 1)
        yield day, span
        day += timedelta(days=span)


def quota_used(client: httpx.Client, key: str) -> int:
    try:
        return int(client.get(STATUS, params={"MAP_KEY": key}, timeout=30).json()["current_transactions"])
    except Exception:  # noqa: BLE001 - status is advisory; never block on it
        return 0


def fetch_one(client: httpx.Client, key: str, region: str, source: str, bbox: str, day: date, span: int) -> dict:
    url = f"{API}/{quote(key, safe='')}/{source}/{bbox}/{span}/{day.isoformat()}"
    last_error = ""
    for attempt in range(5):
        try:
            response = client.get(url, timeout=httpx.Timeout(180.0, connect=10.0))
            if response.status_code == 429 or b"exceeded" in response.content[:300].lower():
                time.sleep(60 * (attempt + 1))
                last_error = "rate limited"
                continue
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                time.sleep(5 * (attempt + 1))
                continue
            rows, _ = firms._validate_csv(response.content)
            digest = hashlib.sha256(response.content).hexdigest()
            name = f"{day.isoformat()}_{span}d.csv.gz"
            folder = archive.RAW_DIR / region / source
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(gzip.compress(response.content, mtime=0))
            return {"source": source, "start": day.isoformat(), "end": (day + timedelta(days=span - 1)).isoformat(),
                    "days": span, "rows": rows, "sha256": digest, "file": name, "status": "ok",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        except firms.FirmsFailure as exc:
            last_error = exc.code
            time.sleep(5 * (attempt + 1))
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
            time.sleep(5 * (attempt + 1))
    return {"source": source, "start": day.isoformat(), "end": (day + timedelta(days=span - 1)).isoformat(),
            "days": span, "status": "failed", "error": last_error}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("region", choices=sorted(archive.REGIONS))
    parser.add_argument("--sources", default="MODIS_SP,VIIRS_SNPP_SP")
    parser.add_argument("--start", default="2012-01-20")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args()

    region = args.region
    archive.region_dir(region).mkdir(parents=True, exist_ok=True)
    if not args.aggregate_only:
        key = (dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("FIRMS_MAP_KEY") or "").strip()
        if not key:
            raise SystemExit("FIRMS_MAP_KEY is not configured in backend/.env")
        bbox = ",".join(str(v) for v in archive.REGIONS[region]["bbox"])
        done = {(e["source"], e["start"]) for e in archive.read_manifest(region) if e.get("status") == "ok"}
        todo = [(s, d, n) for s in args.sources.split(",") for d, n in chunks(date.fromisoformat(args.start), date.fromisoformat(args.end))
                if (s, d.isoformat()) not in done]
        print(f"{region}: {len(todo)} chunks to fetch ({len(done)} already done)", flush=True)
        started = time.time()
        completed = failed = rows = 0
        with httpx.Client(headers={"Accept": "text/csv"}, follow_redirects=False) as client, \
                ThreadPoolExecutor(max_workers=args.workers) as pool, \
                archive.manifest_path(region).open("a") as manifest:
            pending = iter(todo)
            futures = {}

            def submit_next() -> bool:
                item = next(pending, None)
                if item is None:
                    return False
                futures[pool.submit(fetch_one, client, key, region, item[0], bbox, item[1], item[2])] = item
                return True

            for _ in range(args.workers * 2):
                submit_next()
            while futures:
                for future in as_completed(list(futures)):
                    futures.pop(future)
                    entry = future.result()
                    with lock:
                        manifest.write(json.dumps(entry) + "\n")
                        manifest.flush()
                    completed += 1
                    if entry["status"] == "ok":
                        rows += entry["rows"]
                    else:
                        failed += 1
                    if completed % 25 == 0:
                        used = quota_used(client, key)
                        rate = completed / max(time.time() - started, 1)
                        eta = (len(todo) - completed) / max(rate, 1e-6) / 60
                        print(f"  {completed}/{len(todo)} chunks, {rows:,} rows, {failed} failed, quota {used}/5000, ~{eta:.0f} min left", flush=True)
                        while used > QUOTA_PAUSE_AT:
                            print(f"  pausing for quota ({used}/5000)", flush=True)
                            time.sleep(60)
                            used = quota_used(client, key)
                    submit_next()
                    break
        print(f"{region}: fetched {completed} chunks, {rows:,} rows, {failed} failed", flush=True)
    totals = archive.aggregate(region)
    print(f"{region}: aggregated {totals['rows']:,} detections ({totals['type0']:,} presumed vegetation fire)", flush=True)
    print(json.dumps(archive.coverage(region)), flush=True)


if __name__ == "__main__":
    main()
