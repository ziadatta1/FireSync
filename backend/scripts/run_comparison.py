"""Reproduce the global sensor-comparison results quoted in docs/METHODOLOGY.md.

    backend/.venv/bin/python backend/scripts/run_comparison.py

Writes docs/comparison_results.json and prints a Markdown summary table.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import compare, global_data  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNS = [
    ("noaa20", "noaa21", None, 60, cell) for cell in (0.05, 0.1, 0.25)
] + [
    ("modis", "noaa20", "A", 180, 0.1),
    ("modis", "noaa21", "A", 180, 0.1),
    ("modis", "noaa20", None, 60, 0.1),
    ("modis", "noaa21", None, 60, 0.1),
]


def main() -> None:
    results = []
    print("| Pair | Grid | Window | Pairs | median abs dt | co-det A | co-det B | Pearson log | Spearman | median ratio B/A | Calibration | held-out MAE fit / 1:1 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for a, b, sat, window, cell in RUNS:
        r = compare.compare(a, b, global_data.Selection(), cell, window, sat)
        r["frp"].pop("scatter_sample_log10", None)
        results.append(r)
        cal = r["calibration"]
        fit = (
            f"slope {cal['slope']} [{cal['slope_95ci'][0]}, {cal['slope_95ci'][1]}], int {cal['intercept']}"
            if cal["status"] == "fitted" else cal["status"]
        )
        mae = (
            f"{cal['validation_fitted']['median_abs_error_log10']} / {cal['validation_identity_baseline']['median_abs_error_log10']}"
            if cal["status"] == "fitted" else "—"
        )
        label = f"{global_data.SOURCE_LABELS[a]}{' (Aqua)' if sat == 'A' else ''} ↔ {global_data.SOURCE_LABELS[b]}"
        print(
            f"| {label} | {cell}° | ±{window} min | {r['timing']['pairs']:,} | {r['timing']['median_abs_dt_min']} min | "
            f"{r['a_side']['conditional_co_detection_rate']} | {r['b_side']['conditional_co_detection_rate']} | "
            f"{r['frp'].get('pearson_r_log10')} | {r['frp'].get('spearman_rho')} | {r['frp'].get('median_ratio_b_over_a')} "
            f"{r['frp'].get('median_ratio_95ci')} | {fit} | {mae} |"
        )
    out = ROOT / "docs" / "comparison_results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1) + "\n")
    print(f"\nWrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
