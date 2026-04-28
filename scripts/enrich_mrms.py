#!/usr/bin/env python3
"""
MRMS enrichment for the Flash Flood Guidance Map project.

Default behavior is intentionally LIGHTWEIGHT:
- checks archived MRMS directory availability around each event
- writes MRMS_HOURS_FOUND so we can verify archive coverage without downloading GRIB2 files

Optional real sampling mode:
- use --sample-grib
- downloads archived hourly MRMS QPE GRIB2 files
- samples nearest grid point
- computes max 1h/3h/6h/12h QPE in both mm and inches
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

try:
    import xarray as xr
except Exception:
    xr = None

PRODUCTS = ["MultiSensor_QPE_01H_Pass2", "GaugeCorr_QPE_01H"]
ARCHIVE_BASE = "https://mtarchive.geol.iastate.edu"
DEFAULT_SEARCH_HOURS = 6
DEFAULT_MAX_EVENTS = 10
MM_PER_INCH = 25.4

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "flash-flood-guidance-map/0.1"})


def mm_to_in(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value) / MM_PER_INCH


def parse_iso_utc(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%m-%d-%Y %H:%M",
        "%m-%d-%Y",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None


def hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def mrms_dir_url(valid_time: datetime, product: str) -> str:
    return f"{ARCHIVE_BASE}/{valid_time:%Y/%m/%d}/mrms/ncep/{product}/"


def candidate_urls(valid_time: datetime) -> list[tuple[str, str]]:
    stamp = valid_time.strftime("%Y%m%d-%H0000")
    return [
        (product, f"{mrms_dir_url(valid_time, product)}{product}_00.00_{stamp}.grib2.gz")
        for product in PRODUCTS
    ]


def url_exists(url: str, timeout: int = 12) -> bool:
    try:
        r = SESSION.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return True
        if r.status_code in (403, 405, 501):
            r = SESSION.get(url, timeout=timeout, headers={"Range": "bytes=0-0"}, stream=True)
            return r.status_code in (200, 206)
        return False
    except Exception:
        return False


def count_mrms_hours_found(event_time: datetime, search_hours: int) -> tuple[int, str]:
    anchor = hour_floor(event_time)
    found_hours = 0
    products_used: set[str] = set()

    for h in range(-search_hours, search_hours + 1):
        vt = anchor + timedelta(hours=h)
        for product, url in candidate_urls(vt):
            if url_exists(url):
                products_used.add(product)
                found_hours += 1
                break

    return found_hours, ",".join(sorted(products_used))


def download_first_available(valid_time: datetime, cache_dir: Path) -> tuple[Path | None, str | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for product, url in candidate_urls(valid_time):
        gz_path = cache_dir / Path(url).name
        grib_path = cache_dir / gz_path.name.replace(".gz", "")

        if grib_path.exists() and grib_path.stat().st_size > 0:
            return grib_path, product

        try:
            r = SESSION.get(url, timeout=60)
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


def open_first_data_array(grib_path: Path):
    if xr is None:
        raise RuntimeError("xarray/cfgrib is not installed. Run without --sample-grib or install MRMS GRIB dependencies.")

    ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    data_vars = list(ds.data_vars)
    if not data_vars:
        raise ValueError(f"No data variables found in {grib_path}")
    return ds[data_vars[0]]


def sample_nearest(da: Any, lat: float, lon: float) -> float | None:
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
        if np.sum(~np.isnan(chunk)) < max(1, window // 2):
            continue
        s = np.nansum(chunk)
        if np.isnan(best) or s > best:
            best = s
    return None if np.isnan(best) else float(best)


def load_events(path: Path) -> list[dict[str, Any]]:
    gj = json.loads(path.read_text())
    events: list[dict[str, Any]] = []
    for feat in gj.get("features", []):
        props = dict(feat.get("properties", {}))
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        props["_lon"] = coords[0]
        props["_lat"] = coords[1]
        events.append(props)
    return events


def event_datetime(event: dict[str, Any]) -> datetime | None:
    dt = parse_iso_utc(event.get("event_datetime_utc"))
    if dt:
        return dt

    date_value = event.get("BEGIN_DATE") or event.get("begin_date") or event.get("Begin Date")
    time_value = event.get("BEGIN_TIME") or event.get("begin_time") or event.get("Begin Time") or "00:00"
    if date_value:
        combined = f"{date_value} {time_value}"
        dt = parse_iso_utc(combined)
        if dt:
            return dt
        return parse_iso_utc(date_value)

    return None


def base_output_row(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "EVENT_ID": event.get("EVENT_ID") or event.get("event_id") or "",
        "event_datetime_utc": event.get("event_datetime_utc") or "",
        "Latitude": float(event["_lat"]),
        "Longitude": float(event["_lon"]),
        "MRMS_HOURS_FOUND": 0,
        "mrms_hours_sampled": 0,
        "mrms_product_used": "",
        "max_1h_qpe_mm": None,
        "max_3h_qpe_mm": None,
        "max_6h_qpe_mm": None,
        "max_12h_qpe_mm": None,
        "max_1h_qpe_in": None,
        "max_3h_qpe_in": None,
        "max_6h_qpe_in": None,
        "max_12h_qpe_in": None,
    }


def process_event_coverage(event: dict[str, Any], search_hours: int) -> dict[str, Any]:
    row = base_output_row(event)
    dt = event_datetime(event)
    if not dt:
        return row

    row["event_datetime_utc"] = dt.isoformat().replace("+00:00", "Z")
    hours_found, products = count_mrms_hours_found(dt, search_hours)
    row["MRMS_HOURS_FOUND"] = hours_found
    row["mrms_product_used"] = products
    return row


def process_event_grib(event: dict[str, Any], cache_dir: Path, hours_before: int, hours_after: int) -> dict[str, Any]:
    row = base_output_row(event)
    event_id = row["EVENT_ID"]
    lat = row["Latitude"]
    lon = row["Longitude"]
    dt = event_datetime(event)

    if not dt:
        return row

    row["event_datetime_utc"] = dt.isoformat().replace("+00:00", "Z")
    anchor = hour_floor(dt)
    valid_times = [anchor + timedelta(hours=h) for h in range(-hours_before, hours_after + 1)]
    hourly: list[float | None] = []
    products_used: set[str] = set()

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

    max_1h_mm = rolling_sum(hourly, 1)
    max_3h_mm = rolling_sum(hourly, 3)
    max_6h_mm = rolling_sum(hourly, 6)
    max_12h_mm = rolling_sum(hourly, 12)

    row["MRMS_HOURS_FOUND"] = int(sum(v is not None for v in hourly))
    row["mrms_hours_sampled"] = int(sum(v is not None for v in hourly))
    row["mrms_product_used"] = ",".join(sorted(p for p in products_used if p))
    row["max_1h_qpe_mm"] = max_1h_mm
    row["max_3h_qpe_mm"] = max_3h_mm
    row["max_6h_qpe_mm"] = max_6h_mm
    row["max_12h_qpe_mm"] = max_12h_mm
    row["max_1h_qpe_in"] = mm_to_in(max_1h_mm)
    row["max_3h_qpe_in"] = mm_to_in(max_3h_mm)
    row["max_6h_qpe_in"] = mm_to_in(max_6h_mm)
    row["max_12h_qpe_in"] = mm_to_in(max_12h_mm)
    return row


def filter_events(events: list[dict[str, Any]], start_year: int | None, end_year: int | None) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    skipped = 0

    for event in events:
        dt = event_datetime(event)
        if not dt:
            skipped += 1
            continue
        if start_year is not None and dt.year < start_year:
            continue
        if end_year is not None and dt.year > end_year:
            continue
        filtered.append(event)

    if skipped:
        print(f"[WARN] Skipped {skipped:,} event(s) with unparseable dates before MRMS processing")
    return filtered


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich flash flood events with archived MRMS coverage or sampled QPE.")
    ap.add_argument("--events", default="docs/data/flash_flood_events.geojson")
    ap.add_argument("--out", default="docs/data/mrms_event_metrics.csv")
    ap.add_argument("--cache", default=".cache/mrms")
    ap.add_argument("--search-hours", type=int, default=DEFAULT_SEARCH_HOURS, help="Lightweight mode: check ±N hours around event.")
    ap.add_argument("--hours-before", type=int, default=12, help="GRIB sampling mode: hours before event.")
    ap.add_argument("--hours-after", type=int, default=1, help="GRIB sampling mode: hours after event.")
    ap.add_argument("--limit", type=int, default=DEFAULT_MAX_EVENTS, help="Default small limit so you don't accidentally hammer the archive.")
    ap.add_argument("--all-events", action="store_true", help="Process all events instead of the default small limit.")
    ap.add_argument("--sample-grib", action="store_true", help="Download/sample GRIB2 files instead of lightweight coverage check.")
    ap.add_argument("--start-year", type=int, default=None, help="Only process events at or after this UTC year.")
    ap.add_argument("--end-year", type=int, default=None, help="Only process events at or before this UTC year.")
    args = ap.parse_args()

    events = load_events(Path(args.events))
    original_count = len(events)
    events = filter_events(events, args.start_year, args.end_year)
    filtered_count = len(events)

    if not args.all_events and args.limit:
        events = events[: args.limit]

    cache_dir = Path(args.cache)
    rows: list[dict[str, Any]] = []

    mode = "GRIB sampling" if args.sample_grib else "lightweight coverage check"
    print(f"[INFO] Loaded {original_count:,} event(s); {filtered_count:,} remain after year filtering")
    print(f"[INFO] Running MRMS {mode} for {len(events):,} event(s)")

    for i, event in enumerate(events, 1):
        print(f"[INFO] {i}/{len(events)} EVENT_ID={event.get('EVENT_ID') or event.get('event_id')}")
        if args.sample_grib:
            rows.append(process_event_grib(event, cache_dir, args.hours_before, args.hours_after))
        else:
            rows.append(process_event_coverage(event, args.search_hours))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"[OK] Wrote MRMS metrics for {len(rows):,} events to {out}")


if __name__ == "__main__":
    main()
