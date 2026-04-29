#!/usr/bin/env python3
"""
Build empirical 1-hour and 3-hour flash flood threshold grids from MRMS-enriched events.

Input:
  docs/data/mrms_event_metrics.csv

Outputs:
  docs/data/threshold_grid_1h.geojson
  docs/data/threshold_grid_3h.geojson
  docs/data/threshold_grid.geojson

Notes:
- threshold_grid.geojson is kept as the legacy 3-hour output so the existing frontend
  does not break.
- Threshold calculations are inch-based.
- Suspiciously low rainfall thresholds are flagged but not deleted.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


INPUT = Path("docs/data/mrms_event_metrics.csv")

OUTPUT_1H = Path("docs/data/threshold_grid_1h.geojson")
OUTPUT_3H = Path("docs/data/threshold_grid_3h.geojson")
OUTPUT_LEGACY = Path("docs/data/threshold_grid.geojson")

GRID_SIZE = 0.05
MIN_EVENTS_PER_CELL = 1

# QC sanity thresholds. These are intentionally conservative.
# We flag suspicious values instead of deleting them.
MIN_REALISTIC_1H_THRESHOLD_IN = 0.75
MIN_REALISTIC_3H_THRESHOLD_IN = 1.20

MAX_REASONABLE_1H_THRESHOLD_IN = 15.0
MAX_REASONABLE_3H_THRESHOLD_IN = 30.0


def threshold_category(value_in: float | None, duration: str) -> str:
    if value_in is None or not np.isfinite(value_in):
        return "unknown"

    if duration == "1h":
        if value_in < 1.0:
            return "very_high_sensitivity"
        if value_in < 1.5:
            return "high_sensitivity"
        if value_in < 2.0:
            return "moderate_sensitivity"
        if value_in < 3.0:
            return "lower_sensitivity"
        return "least_sensitive"

    if value_in < 2.0:
        return "high_sensitivity"
    if value_in < 3.0:
        return "moderate_sensitivity"
    if value_in < 4.0:
        return "lower_sensitivity"
    return "least_sensitive"


def qc_for_threshold(value_in: float | None, duration: str, event_count: int) -> tuple[str, str, bool]:
    """
    Return:
      qc_flag, qc_reason, used_for_surface

    used_for_surface=False means the point should remain visible for review but should
    not feed interpolation/contours.
    """
    reasons = []

    if value_in is None or not np.isfinite(value_in):
        return "missing_threshold", "missing threshold value", False

    if duration == "1h":
        if value_in < MIN_REALISTIC_1H_THRESHOLD_IN:
            reasons.append(f"1h threshold below {MIN_REALISTIC_1H_THRESHOLD_IN:.2f} inch")
        if value_in > MAX_REASONABLE_1H_THRESHOLD_IN:
            reasons.append(f"1h threshold above {MAX_REASONABLE_1H_THRESHOLD_IN:.1f} inches")
    else:
        if value_in < MIN_REALISTIC_3H_THRESHOLD_IN:
            reasons.append(f"3h threshold below {MIN_REALISTIC_3H_THRESHOLD_IN:.2f} inch")
        if value_in > MAX_REASONABLE_3H_THRESHOLD_IN:
            reasons.append(f"3h threshold above {MAX_REASONABLE_3H_THRESHOLD_IN:.1f} inches")

    if event_count < MIN_EVENTS_PER_CELL:
        reasons.append(f"fewer than {MIN_EVENTS_PER_CELL} supporting events")

    if reasons:
        return "suspect_threshold", "; ".join(reasons), False

    return "ok", "ok", True


def safe_round(value: float | None, ndigits: int = 2):
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), ndigits)


def build_duration_grid(df: pd.DataFrame, duration: str, value_col: str, output: Path) -> int:
    required = ["EVENT_ID", "Latitude", "Longitude", value_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required column(s) for {duration}: {missing}")

    work = df.copy()

    work["Latitude"] = pd.to_numeric(work["Latitude"], errors="coerce")
    work["Longitude"] = pd.to_numeric(work["Longitude"], errors="coerce")
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce")

    work = work.dropna(subset=["Latitude", "Longitude", value_col])

    max_reasonable = MAX_REASONABLE_1H_THRESHOLD_IN if duration == "1h" else MAX_REASONABLE_3H_THRESHOLD_IN
    work = work[(work[value_col] >= 0) & (work[value_col] <= max_reasonable)]

    if work.empty:
        raise ValueError(f"No usable MRMS rows after cleaning for {duration}.")

    work["grid_lat"] = (work["Latitude"] / GRID_SIZE).round() * GRID_SIZE
    work["grid_lon"] = (work["Longitude"] / GRID_SIZE).round() * GRID_SIZE

    if "severity_weight" not in work.columns:
        work["severity_weight"] = 1

    work["severity_weight"] = (
        pd.to_numeric(work["severity_weight"], errors="coerce")
        .fillna(1)
        .clip(lower=1)
    )

    rows = []

    for (grid_lat, grid_lon), g in work.groupby(["grid_lat", "grid_lon"]):
        event_count = len(g)
        if event_count < MIN_EVENTS_PER_CELL:
            continue

        values = g[value_col].to_numpy(dtype=float)
        weights = g["severity_weight"].to_numpy(dtype=float)

        median_val = float(np.median(values))
        mean_val = float(np.mean(values))

        try:
            weighted_mean_val = float(np.average(values, weights=weights))
        except Exception:
            weighted_mean_val = mean_val

        # Keep median as the empirical threshold. It is less fragile than mean
        # when one MRMS/event sample is a garbage fire.
        threshold_in = median_val

        qc_flag, qc_reason, used_for_surface = qc_for_threshold(
            threshold_in,
            duration=duration,
            event_count=event_count,
        )

        rows.append({
            "grid_lat": float(grid_lat),
            "grid_lon": float(grid_lon),
            f"threshold_{duration}_in": threshold_in,
            f"median_{duration}_in": median_val,
            f"mean_{duration}_in": mean_val,
            f"weighted_mean_{duration}_in": weighted_mean_val,
            f"min_{duration}_in": float(np.min(values)),
            f"max_{duration}_in": float(np.max(values)),
            "event_count": int(event_count),
            "duration": duration,
            "category": threshold_category(threshold_in, duration),
            "qc_flag": qc_flag,
            "qc_reason": qc_reason,
            "used_for_surface": bool(used_for_surface),
        })

    features = []

    for row in rows:
        duration = row["duration"]

        props = {
            "duration": duration,
            f"threshold_{duration}_in": safe_round(row[f"threshold_{duration}_in"]),
            f"median_{duration}_in": safe_round(row[f"median_{duration}_in"]),
            f"mean_{duration}_in": safe_round(row[f"mean_{duration}_in"]),
            f"weighted_mean_{duration}_in": safe_round(row[f"weighted_mean_{duration}_in"]),
            f"min_{duration}_in": safe_round(row[f"min_{duration}_in"]),
            f"max_{duration}_in": safe_round(row[f"max_{duration}_in"]),
            "event_count": row["event_count"],
            "threshold_category": row["category"],
            "qc_flag": row["qc_flag"],
            "qc_reason": row["qc_reason"],
            "used_for_surface": row["used_for_surface"],
        }

        # Compatibility aliases. These make future frontend code easier and
        # allow generic display code to use threshold_in regardless of duration.
        props["threshold_in"] = props[f"threshold_{duration}_in"]
        props["median_in"] = props[f"median_{duration}_in"]
        props["mean_in"] = props[f"mean_{duration}_in"]
        props["weighted_mean_in"] = props[f"weighted_mean_{duration}_in"]
        props["min_in"] = props[f"min_{duration}_in"]
        props["max_in"] = props[f"max_{duration}_in"]

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row["grid_lon"], row["grid_lat"]],
            },
            "properties": props,
        })

    geojson = {
        "type": "FeatureCollection",
        "name": f"empirical_flash_flood_threshold_grid_{duration}",
        "features": features,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(geojson, indent=2), encoding="utf-8")

    print(f"[OK] {duration}: Read {len(work):,} usable MRMS sampled event row(s)")
    print(f"[OK] {duration}: Wrote {len(features):,} threshold grid point(s)")
    print(f"[OK] {duration}: Output: {output}")

    return len(features)


def main() -> None:
    if not INPUT.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT}")

    df = pd.read_csv(INPUT)

    count_1h = build_duration_grid(df, "1h", "max_1h_qpe_in", OUTPUT_1H)
    count_3h = build_duration_grid(df, "3h", "max_3h_qpe_in", OUTPUT_3H)

    # Legacy compatibility: current frontend expects docs/data/threshold_grid.geojson.
    # Keep this as the 3-hour grid until the frontend gets a duration selector.
    shutil.copyfile(OUTPUT_3H, OUTPUT_LEGACY)
    print(f"[OK] Legacy 3h copy: {OUTPUT_LEGACY}")

    print("[OK] Phase 1 threshold grid build complete")
    print(f"[OK] 1h grid points: {count_1h:,}")
    print(f"[OK] 3h grid points: {count_3h:,}")


if __name__ == "__main__":
    main()
