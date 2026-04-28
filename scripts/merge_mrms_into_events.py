#!/usr/bin/env python3
"""
Merge MRMS event metrics into the map event GeoJSON.

Input:
  docs/data/flash_flood_events.geojson
  docs/data/mrms_event_metrics.csv

Output:
  docs/data/flash_flood_events.geojson by default
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def clean_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def event_id_key(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        return str(int(float(s)))
    except Exception:
        return s


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge MRMS metrics into flash flood event GeoJSON.")
    ap.add_argument("--events", default="docs/data/flash_flood_events.geojson")
    ap.add_argument("--mrms", default="docs/data/mrms_event_metrics.csv")
    ap.add_argument("--out", default="docs/data/flash_flood_events.geojson")
    args = ap.parse_args()

    events_path = Path(args.events)
    mrms_path = Path(args.mrms)
    out_path = Path(args.out)

    if not events_path.exists():
        raise FileNotFoundError(f"Events GeoJSON not found: {events_path}")
    if not mrms_path.exists():
        raise FileNotFoundError(f"MRMS metrics CSV not found: {mrms_path}")

    gj = json.loads(events_path.read_text(encoding="utf-8"))
    df = pd.read_csv(mrms_path)

    if "EVENT_ID" not in df.columns:
        raise ValueError("MRMS CSV is missing EVENT_ID")

    df["_event_id_key"] = df["EVENT_ID"].map(event_id_key)
    df = df[df["_event_id_key"] != ""].copy()

    rainfall_cols = [
        "MRMS_HOURS_FOUND",
        "mrms_hours_sampled",
        "mrms_product_used",
        "max_1h_qpe_mm",
        "max_3h_qpe_mm",
        "max_6h_qpe_mm",
        "max_12h_qpe_mm",
        "max_1h_qpe_in",
        "max_3h_qpe_in",
        "max_6h_qpe_in",
        "max_12h_qpe_in",
    ]

    existing_cols = [c for c in rainfall_cols if c in df.columns]
    lookup = df.set_index("_event_id_key")[existing_cols].to_dict(orient="index")

    merged = 0
    features = gj.get("features", [])

    for feature in features:
        props = feature.setdefault("properties", {})
        eid = event_id_key(props.get("EVENT_ID") or props.get("event_id"))

        if not eid or eid not in lookup:
            continue

        metrics = lookup[eid]
        for col, value in metrics.items():
            props[col] = clean_value(value)

        props["mrms_enriched"] = True
        merged += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(gj, indent=2), encoding="utf-8")

    print(f"[OK] Loaded {len(features):,} event feature(s)")
    print(f"[OK] Loaded {len(df):,} MRMS metric row(s)")
    print(f"[OK] Merged MRMS metrics into {merged:,} event feature(s)")
    print(f"[OK] Output: {out_path}")


if __name__ == "__main__":
    main()
