"""Read-only audit of the downloaded FIRMS standard-processing archive.

    backend/.venv/bin/python backend/scripts/audit_archive.py                 # all regions with a manifest
    backend/.venv/bin/python backend/scripts/audit_archive.py --out docs/archive_audit.json

Checks, per region and source, without modifying anything:
  * chunk completeness against the expected 5-day schedule (ok / failed / not yet fetched / duplicated)
  * raw file present and SHA-256 of the decompressed payload equal to the manifest
  * row count equal to the manifest
  * records, date coverage, monthly record counts, months with missing days
  * duplicate records, invalid coordinates, timestamps outside the chunk's date range
  * counts by FIRMS `type`, by satellite, and retained after type == 0

Retries inside the fetcher are not logged per chunk; only the final status of each chunk is.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import archive  # noqa: E402

START, END = date(2012, 1, 20), date(2025, 12, 31)


def expected_chunks():
    day = START
    while day <= END:
        span = min(5, (END - day).days + 1)
        yield day.isoformat(), span
        day += timedelta(days=span)


def audit_source(region: str, source: str, entries: list[dict]) -> dict:
    expected = dict(expected_chunks())
    by_start = defaultdict(list)
    for e in entries:
        by_start[e["start"]].append(e)
    ok = {s: es for s, es in by_start.items() if any(x.get("status") == "ok" for x in es)}
    failed_only = sorted(s for s, es in by_start.items() if s not in ok)
    failed_then_ok = sorted(s for s, es in by_start.items() if s in ok and any(x.get("status") != "ok" for x in es))
    duplicated_ok = sorted(s for s, es in ok.items() if sum(x.get("status") == "ok" for x in es) > 1)
    missing = sorted(s for s in expected if s not in by_start)
    unexpected = sorted(s for s in by_start if s not in expected)

    hash_bad, rows_bad, files_missing = [], [], []
    frames = []
    for start in sorted(ok):
        e = next(x for x in ok[start] if x.get("status") == "ok")
        path = archive.RAW_DIR / region / source / e["file"]
        if not path.is_file():
            files_missing.append(start)
            continue
        payload = gzip.decompress(path.read_bytes())
        if hashlib.sha256(payload).hexdigest() != e["sha256"]:
            hash_bad.append(start)
            continue
        if not e["rows"]:
            continue
        df = pd.read_csv(io.BytesIO(payload), dtype={"acq_time": str, "satellite": str, "confidence": str, "version": str, "daynight": str})
        if len(df) != e["rows"]:
            rows_bad.append(start)
        df["_chunk_start"], df["_chunk_end"] = e["start"], e["end"]
        frames.append(df)

    covered_days = set()
    for start in ok:
        d0 = date.fromisoformat(start)
        for i in range(expected.get(start, 0) or next(x for x in ok[start] if x.get("status") == "ok")["days"]):
            covered_days.add((d0 + timedelta(days=i)).isoformat())
    all_days = [(START + timedelta(days=i)).isoformat() for i in range((END - START).days + 1)]
    missing_days = [d for d in all_days if d not in covered_days]
    months_missing_days = sorted({d[:7] for d in missing_days})

    result = {
        "chunks_expected": len(expected),
        "chunks_ok": len(ok),
        "chunks_failed_final": len(failed_only),
        "chunks_failed_then_succeeded": len(failed_then_ok),
        "chunks_duplicated_ok": len(duplicated_ok),
        "chunks_not_yet_fetched": len(missing),
        "chunks_unexpected": len(unexpected),
        "retries_logged": "no (fetcher retries up to 5x internally; only final status is recorded)",
        "raw_files_missing": files_missing,
        "sha256_mismatch": hash_bad,
        "row_count_mismatch": rows_bad,
        "days_not_covered": len(missing_days),
        "first_day_not_covered": missing_days[0] if missing_days else None,
        "months_with_missing_days": months_missing_days if len(months_missing_days) <= 24 else months_missing_days[:3] + ["..."] + months_missing_days[-3:],
        "months_with_missing_days_count": len(months_missing_days),
    }
    if not frames:
        return result
    df = pd.concat(frames, ignore_index=True)
    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    bbox = archive.REGIONS[region]["bbox"]
    t = df["acq_time"].str.zfill(4)
    hh, mm = pd.to_numeric(t.str[:2], errors="coerce"), pd.to_numeric(t.str[2:], errors="coerce")
    bad_time = (hh.isna() | mm.isna() | (hh > 23) | (mm > 59))
    date_ok = pd.to_datetime(df["acq_date"], format="%Y-%m-%d", errors="coerce").notna()
    outside_chunk = (df["acq_date"] < df["_chunk_start"]) | (df["acq_date"] > df["_chunk_end"])
    key_cols = [c for c in df.columns if not c.startswith("_")]
    monthly = df.groupby(df["acq_date"].str.slice(0, 7)).size()
    result.update({
        "records": int(len(df)),
        "date_coverage": [str(df["acq_date"].min()), str(df["acq_date"].max())],
        "duplicate_records": int(df.duplicated(subset=key_cols).sum()),
        "invalid_coordinates": int((lat.isna() | lon.isna() | (lat.abs() > 90) | (lon.abs() > 180)).sum()),
        "outside_region_bbox": int(((lat < bbox[1]) | (lat > bbox[3]) | (lon < bbox[0]) | (lon > bbox[2])).sum()),
        "invalid_dates": int((~date_ok).sum()),
        "invalid_times": int(bad_time.sum()),
        "dates_outside_their_chunk": int(outside_chunk.sum()),
        "by_type": {str(k): int(v) for k, v in sorted(Counter(df["type"]).items())},
        "type0_records": int((df["type"] == 0).sum()),
        "type0_fraction": round(float((df["type"] == 0).mean()), 4),
        "by_satellite": {str(k): int(v) for k, v in sorted(Counter(df["satellite"]).items())},
        "versions": {str(k): int(v) for k, v in sorted(Counter(df["version"]).items())},
        "months_with_records": int(monthly.size),
        "monthly_records_min_median_max": [int(monthly.min()), float(monthly.median()), int(monthly.max())],
        "monthly_records": {k: int(v) for k, v in monthly.items()},
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    args = parser.parse_args()
    report = {}
    for region in archive.REGIONS:
        entries = archive.read_manifest(region)
        if not entries:
            continue
        report[region] = {s: audit_source(region, s, [e for e in entries if e["source"] == s]) for s in ("MODIS_SP", "VIIRS_SNPP_SP")}
    for region, sources in report.items():
        for source, r in sources.items():
            short = {k: v for k, v in r.items() if k != "monthly_records"}
            print(f"== {region} / {source}")
            print(json.dumps(short, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1) + "\n")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
