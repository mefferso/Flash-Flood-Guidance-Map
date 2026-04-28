#!/usr/bin/env python3
"""
Build an empirical 3-hour flash flood threshold grid from MRMS-enriched events.

Input:
  docs/data/mrms_event_metrics.csv

Output:
  docs/data/threshold_grid.geojson

This script uses inch-based rainfall columns. Raw MRMS values may be retained in mm
elsewhere, but threshold calculations are in inches.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


INPUT = Path("docs/data/mrms_event_metrics.csv")
OUTPUT = Path("docs/data/threshold_grid.geojson")
GRID_SIZE = 0.05
MIN_EVENTS_PER_CELL = 1


def threshold_category(value_in):
    if value_in is None:
        return "unknown"
    if value_in < 2.0:
        return "high_sensitivity"
    if value_in < 3.0:
        return "moderate_sensitivity"
    if value_in < 4.0:
        return "lower_sensitivity"
    return "least_sensitive"


def main() -> None:
    if not INPUT.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT}")

    df = pd.read_csv(INPUT)

    required = ["EVENT_ID", "Latitude", "Longitude", "max_3h_qpe_in"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")

    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")
    df["max_3h_qpe_in"] = pd.to_numeric(df["max_3h_qpe_in"], errors="coerce")

    df = df.dropna(subset=["Latitude", "Longitude", "max_3h_qpe_in"])
    df = df[(df["max_3h_qpe_in"] >= 0) & (df["max_3h_qpe_in"] <= 30)]

    if df.empty:
        raise ValueError("No usable MRMS rows after cleaning.")

    df["grid_lat"] = (df["Latitude"] / GRID_SIZE).round() * GRID_SIZE
    df["grid_lon"] = (df["Longitude"] / GRID_SIZE).round() * GRID_SIZE

    if "severity_weight" not in df.columns:
        df["severity_weight"] = 1

    df["severity_weight"] = (
        pd.to_numeric(df["severity_weight"], errors="coerce")
        .fillna(1)
        .clip(lower=1)
    )

    rows = []

    for (grid_lat, grid_lon), g in df.groupby(["grid_lat", "grid_lon"]):
        event_count = len(g)
        if event_count < MIN_EVENTS_PER_CELL:
            continue

        values = g["max_3h_qpe_in"].to_numpy(dtype=float)
        weights = g["severity_weight"].to_numpy(dtype=float)

        median_3h = float(np.median(values))
        mean_3h = float(np.mean(values))

        try:
            weighted_mean_3h = float(np.average(values, weights=weights))
        except Exception:
            weighted_mean_3h = mean_3h

        threshold_3h_in = median_3h

        rows.append({
            "grid_lat": float(grid_lat),
            "grid_lon": float(grid_lon),
            "threshold_3h_in": threshold_3h_in,
            "median_3h_in": median_3h,
            "mean_3h_in": mean_3h,
            "weighted_mean_3h_in": weighted_mean_3h,
            "min_3h_in": float(np.min(values)),
            "max_3h_in": float(np.max(values)),
            "event_count": int(event_count),
            "category": threshold_category(threshold_3h_in),
        })

    features = []
    for row in rows:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row["grid_lon"], row["grid_lat"]],
            },
            "properties": {
                "threshold_3h_in": round(row["threshold_3h_in"], 2),
                "median_3h_in": round(row["median_3h_in"], 2),
                "mean_3h_in": round(row["mean_3h_in"], 2),
                "weighted_mean_3h_in": round(row["weighted_mean_3h_in"], 2),
                "min_3h_in": round(row["min_3h_in"], 2),
                "max_3h_in": round(row["max_3h_in"], 2),
                "event_count": row["event_count"],
                "threshold_category": row["category"],
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "name": "empirical_flash_flood_threshold_grid",
        "features": features,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(geojson, indent=2), encoding="utf-8")

    print(f"[OK] Read {len(df):,} MRMS sampled event row(s) with inch-based 3-hour QPE")
    print(f"[OK] Wrote {len(features):,} threshold grid point(s)")
    print(f"[OK] Output: {OUTPUT}")


if __name__ == "__main__":
    main()
