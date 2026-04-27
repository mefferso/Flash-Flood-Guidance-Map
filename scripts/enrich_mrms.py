#!/usr/bin/env python3
"""
Sample archived MRMS hourly QPE around flash flood events.

This is a starter, not the final boss.

It samples the nearest MRMS grid point for each event and computes:
- max_1h_qpe_mm
- max_3h_qpe_mm
- max_6h_qpe_mm
- max_12h_qpe_mm

Default archive:
Iowa State MTArchive:
https://mtarchive.geol.iastate.edu/YYYY/MM/DD/mrms/ncep/<PRODUCT>/

Products tried:
- MultiSensor_QPE_01H_Pass2
- GaugeCorr_QPE_01H

Why hourly QPE first?
Because a flash flood threshold needs event-scale rainfall accumulation. Instantaneous precip rate can be useful later, but it is noisier and easier to overfit like a raccoon with a spreadsheet.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
import xarray as xr

PRODUCTS = ["MultiSensor_QPE_01H_Pass2", "GaugeCorr_QPE_01H"]
ARCHIVE_BASE = "https://mtarchive.geol.iastate.edu"


def parse_iso_utc(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value)
    try:
        # Handles "+00:00"; convert trailing Z if present.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def candidate_urls(valid_time: datetime) -> list[tuple[str, str]]:
    y = valid_time.strftime("%Y")
    m = valid_time.strftime("%m")
    d = valid_time.strftime("%d")
    stamp = valid_time.strftime("%Y%m%d-%H0000")
    urls = []
    for product in PRODUCTS:
        name = f"{product}_00.00_{stamp}.grib2.gz"
        url = f"{ARCHIVE_BASE}/{y}/{m}/{d}/mrms/ncep/{product}/{name}"
        urls.append((product, url))
    return urls


def download_first_available(valid_time: datetime, cache_dir: Path) -> tuple[Path | None, str | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for product, url in candidate_urls(valid_time):
        gz_path = cache_dir / Path(url).name
        grib_path = cache_dir / gz_path.name.replace(".gz", "")

        if grib_path.exists():
            return grib_path, product

        try:
            r = requests.get(url, timeout=60)
            if r.status_code != 200:
                continue
            gz_path.write_bytes(r.content)
            with gzip.open(gz_path, "rb") as src, open(grib_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return grib_path, product
        except Exception as exc:
            print(f"[WARN] Failed {url}: {exc}")
            continue

    return None, None


def open_first_data_array(grib_path: Path) -> xr.DataArray:
    # indexpath='' avoids stale .idx files in short-lived CI/cache situations.
    ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    data_vars = list(ds.data_vars)
    if not data_vars:
        raise ValueError(f"No data variables found in {grib_path}")
    return ds[data_vars[0]]


def sample_nearest(da: xr.DataArray, lat: float, lon: float) -> float | None:
    coords = da.coords
    lat_name = "latitude" if "latitude" in coords else "lat" if "lat" in coords else None
    lon_name = "longitude" if "longitude" in coords else "lon" if "lon" in coords else None
    if not lat_name or not lon_name:
        raise ValueError(f"Could not find lat/lon coordinates in GRIB: {list(coords)}")

    lon_vals = da[lon_name].values
    sample_lon = lon
    if np.nanmax(lon_vals) > 180 and sample_lon < 0:
        sample_lon = sample_lon + 360

    val = da.sel({lat_name: lat, lon_name: sample_lon}, method="nearest").values
    try:
        f = float(np.asarray(val).squeeze())
    except Exception:
        return None

    # MRMS missing/no-coverage values are often negative.
    if not math.isfinite(f) or f < 0:
        return None

    return f


def rolling_sum(values: list[float | None], window: int) -> float | None:
    clean = [np.nan if v is None else float(v) for v in values]
    if len(clean) < window:
        return None
    arr = np.array(clean, dtype=float)
    best = np.nan
    for i in range(0, len(arr) - window + 1):
        chunk = arr[i:i + window]
        if np.isnan(chunk).all():
            continue
        # Require at least half the window present.
        if np.sum(~np.isnan(chunk)) < max(1, window // 2):
            continue
        s = np.nansum(chunk)
        if np.isnan(best) or s > best:
            best = s
    return None if np.isnan(best) else float(best)


def load_events(path: Path) -> list[dict[str, Any]]:
    gj = json.loads(path.read_text())
    events = []
    for feat in gj.get("features", []):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        props["_lon"] = coords[0]
        props["_lat"] = coords[1]
        events.append(props)
    return events


def process_event(event: dict[str, Any], cache_dir: Path, hours_before: int, hours_after: int) -> dict[str, Any]:
    event_id = event.get("EVENT_ID")
    lat = float(event["_lat"])
    lon = float(event["_lon"])
    dt = parse_iso_utc(event.get("event_datetime_utc"))

    row = {
        "EVENT_ID": event_id,
        "event_datetime_utc": event.get("event_datetime_utc"),
        "Latitude": lat,
        "Longitude": lon,
        "mrms_hours_sampled": 0,
        "mrms_product_used": "",
        "max_1h_qpe_mm": None,
        "max_3h_qpe_mm": None,
        "max_6h_qpe_mm": None,
        "max_12h_qpe_mm": None,
    }

    if not dt:
        return row

    anchor = hour_floor(dt)
    valid_times = [anchor + timedelta(hours=h) for h in range(-hours_before, hours_after + 1)]
    hourly = []
    products_used = set()

    for vt in valid_times:
        grib, product = download_first_available(vt, cache_dir)
        if not grib:
            hourly.append(None)
            continue

        try:
            da = open_first_data_array(grib)
            value = sample_nearest(da, lat, lon)
            hourly.append(value)
            products_used.add(product or "")
        except Exception as exc:
            print(f"[WARN] EVENT_ID={event_id} failed sampling {vt}: {exc}")
            hourly.append(None)

    row["mrms_hours_sampled"] = int(sum(v is not None for v in hourly))
    row["mrms_product_used"] = ",".join(sorted(p for p in products_used if p))
    row["max_1h_qpe_mm"] = rolling_sum(hourly, 1)
    row["max_3h_qpe_mm"] = rolling_sum(hourly, 3)
    row["max_6h_qpe_mm"] = rolling_sum(hourly, 6)
    row["max_12h_qpe_mm"] = rolling_sum(hourly, 12)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="docs/data/flash_flood_events.geojson")
    ap.add_argument("--out", default="docs/data/mrms_event_metrics.csv")
    ap.add_argument("--cache", default=".cache/mrms")
    ap.add_argument("--hours-before", type=int, default=12)
    ap.add_argument("--hours-after", type=int, default=1)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    events = load_events(Path(args.events))
    if args.limit:
        events = events[:args.limit]

    cache_dir = Path(args.cache)
    rows = []
    for i, event in enumerate(events, 1):
        print(f"[INFO] {i}/{len(events)} EVENT_ID={event.get('EVENT_ID')}")
        rows.append(process_event(event, cache_dir, args.hours_before, args.hours_after))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"[OK] Wrote MRMS metrics for {len(rows):,} events to {out}")


if __name__ == "__main__":
    main()
