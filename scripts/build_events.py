#!/usr/bin/env python3
"""
Build flash flood event CSV + GeoJSON for the GitHub Pages map.

Input options:
1. Existing CSV exported from the old Google Sheet / Apps Script workflow.
2. NCEI Storm Events bulk CSV download by year.

Default output:
- docs/data/flash_flood_events.csv
- docs/data/flash_flood_events.geojson
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo

from config import (
    LIX_LA_PARISHES,
    LIX_MS_COUNTIES,
    NCEI_STORMEVENTS_BASE_URL,
)

CENTRAL = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


OUTPUT_COLUMNS = [
    "EVENT_ID", "Parish/County", "BEGIN_LOCATION", "BEGIN_DATE", "BEGIN_TIME",
    "event_datetime_local", "event_datetime_utc",
    "EVENT_TYPE", "DEATHS_DIRECT", "INJURIES_DIRECT", "DAMAGE_PROPERTY_NUM",
    "DAMAGE_CROPS_NUM", "STATE_ABBR", "EPISODE_ID", "INJURIES_INDIRECT",
    "DEATHS_INDIRECT", "SOURCE", "FLOOD_CAUSE", "BEGIN_RANGE", "BEGIN_AZIMUTH",
    "END_RANGE", "END_AZIMUTH", "END_LOCATION", "END_DATE", "END_TIME",
    "BEGIN_LAT", "BEGIN_LON", "END_LAT", "END_LON", "EVENT_NARRATIVE",
    "EPISODE_NARRATIVE", "CITY/CITIES", "STREET(S)", "Latitude", "Longitude",
    "Bayou/Creek/Stream Name", "severity_category", "severity_weight",
]


def normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def is_lix_county_parish(state: str, state_abbr: str, cz_name: str) -> bool:
    cz = normalize_name(cz_name)
    if not cz:
        return False
    st = normalize_name(state)
    abbr = normalize_name(state_abbr)
    if abbr == "LA" or st == "LOUISIANA":
        return cz in LIX_LA_PARISHES
    if abbr == "MS" or st == "MISSISSIPPI":
        return cz in LIX_MS_COUNTIES
    return False


def parse_damage(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().upper()
    if not s:
        return None
    if re.match(r"^-?\d+(\.\d+)?$", s):
        return float(s)
    m = re.match(r"^(-?\d+(\.\d+)?)([KMB])$", s)
    if not m:
        return None
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(3)]
    return float(m.group(1)) * mult


def parse_date_time(date_value: Any, time_value: Any) -> tuple[str, str, str, str]:
    """Return begin date/time strings plus local/UTC ISO datetimes."""
    date_s = str(date_value or "").strip()
    time_s = str(time_value or "").strip()

    if not date_s:
        return "", "", "", ""

    # BEGIN_TIME from Storm Events can be 0, 5, 30, 2359, or HH:MM.
    if re.fullmatch(r"\d{1,4}", time_s):
        time_s = time_s.zfill(4)
        time_s = f"{time_s[:2]}:{time_s[2:]}"
    elif not time_s:
        time_s = "00:00"

    dt_local = None
    for candidate in (f"{date_s} {time_s}", date_s):
        try:
            dt_local = dtparser.parse(candidate)
            break
        except Exception:
            pass

    if dt_local is None:
        return date_s, time_s, "", ""

    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=CENTRAL)

    dt_utc = dt_local.astimezone(UTC)

    return (
        dt_local.strftime("%Y-%m-%d"),
        dt_local.strftime("%H:%M"),
        dt_local.isoformat(),
        dt_utc.isoformat(),
    )


def severity_category(text: Any) -> str:
    t = str(text or "").lower()

    catastrophic = re.compile(
        r"fatalit|drown|killed|death|washed out|record flooding|record levels|historic|"
        r"widespread flash flooding.*(?:\d{3,}|\d{1,3},\d{3,})\s+(?:homes|businesses)|"
        r"over\s+\d{3,}\s+(?:homes|businesses)|approximately\s+\d{3,}\s+(?:homes|businesses)|"
        r"numerous high water rescues|hundreds of high water rescues|"
        r"interstates?\s+\d+\s+and\s+\d+\s+were closed|section of .* highway .* overtopped|"
        r"road washed out|parish hospital"
    )

    if catastrophic.search(t):
        return "catastrophic"
    if re.search(r"rescues?|stranded|trapped|submerged|wash\s*out|evacuated?|widespread", t):
        return "severe"
    if re.search(r"entered|inundated|extensive|impassable|inches.*inside|inches.*in\s+(home|house|business|building|structure)", t):
        return "serious"
    if re.search(r"covering|stalled|closed|overflowed|approached", t):
        return "moderate"
    return "minor"


def severity_weight(category: str) -> int:
    return {
        "catastrophic": 10,
        "severe": 6,
        "serious": 3,
        "moderate": 2,
        "minor": 1,
    }.get(category, 1)


def clean_existing_csv(df: pd.DataFrame) -> pd.DataFrame:
    # Normalize expected columns from the existing sheet export.
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    # Prefer explicit Latitude/Longitude if populated, otherwise BEGIN_LAT/LON.
    df["Latitude"] = pd.to_numeric(df["Latitude"].where(df["Latitude"].notna(), df["BEGIN_LAT"]), errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"].where(df["Longitude"].notna(), df["BEGIN_LON"]), errors="coerce")
    df["BEGIN_LAT"] = pd.to_numeric(df["BEGIN_LAT"], errors="coerce").where(lambda s: s.notna(), df["Latitude"])
    df["BEGIN_LON"] = pd.to_numeric(df["BEGIN_LON"], errors="coerce").where(lambda s: s.notna(), df["Longitude"])

    dates = df.apply(lambda r: parse_date_time(r.get("BEGIN_DATE"), r.get("BEGIN_TIME")), axis=1)
    df["BEGIN_DATE"] = [x[0] for x in dates]
    df["BEGIN_TIME"] = [x[1] for x in dates]
    df["event_datetime_local"] = [x[2] for x in dates]
    df["event_datetime_utc"] = [x[3] for x in dates]

    narrative = df["EVENT_NARRATIVE"].fillna("")
    fallback = df["EPISODE_NARRATIVE"].fillna("")
    cat = [severity_category(a if str(a).strip() else b) for a, b in zip(narrative, fallback)]
    df["severity_category"] = cat
    df["severity_weight"] = [severity_weight(c) for c in cat]

    return df[OUTPUT_COLUMNS].copy()


def fetch_ncei_file_list() -> dict[int, str]:
    html = requests.get(NCEI_STORMEVENTS_BASE_URL, timeout=60).text
    pattern = re.compile(r"StormEvents_details-ftp_v1\.0_d(\d{4})_c\d{8}\.csv\.gz")
    year_map: dict[int, str] = {}
    for m in pattern.finditer(html):
        year = int(m.group(1))
        year_map[year] = NCEI_STORMEVENTS_BASE_URL + m.group(0)
    return year_map


def read_ncei_year(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    with gzip.GzipFile(fileobj=io.BytesIO(r.content)) as gz:
        return pd.read_csv(gz, low_memory=False)


def build_from_ncei(start_year: int, end_year: int) -> pd.DataFrame:
    year_files = fetch_ncei_file_list()
    pieces = []

    for year in range(start_year, end_year + 1):
        url = year_files.get(year)
        if not url:
            print(f"[WARN] Missing NCEI file for {year}")
            continue

        print(f"[INFO] Downloading {year}: {url}")
        raw = read_ncei_year(url)

        # Standardize to uppercase names for safety.
        raw.columns = [c.strip().upper() for c in raw.columns]

        mask = raw["EVENT_TYPE"].astype(str).str.upper().eq("FLASH FLOOD")
        raw = raw.loc[mask].copy()

        raw["CZ_NAME_NORM"] = raw["CZ_NAME"].map(normalize_name)
        raw["STATE_NORM"] = raw.get("STATE", "").map(normalize_name) if "STATE" in raw else ""
        raw["STATE_ABBR_NORM"] = raw.get("STATE_ABBR", "").map(normalize_name) if "STATE_ABBR" in raw else ""

        keep = raw.apply(
            lambda r: is_lix_county_parish(r.get("STATE", ""), r.get("STATE_ABBR", ""), r.get("CZ_NAME", "")),
            axis=1,
        )
        raw = raw.loc[keep].copy()

        if raw.empty:
            continue

        begin_dt = raw.get("BEGIN_DATE_TIME", "")
        end_dt = raw.get("END_DATE_TIME", "")

        # The downloaded details CSV normally has BEGIN_DATE_TIME/END_DATE_TIME.
        begin_parsed = begin_dt.map(lambda x: dtparser.parse(str(x)) if str(x).strip() else None)
        end_parsed = end_dt.map(lambda x: dtparser.parse(str(x)) if str(x).strip() else None)

        out = pd.DataFrame()
        out["EVENT_ID"] = raw.get("EVENT_ID", "")
        out["Parish/County"] = raw.get("CZ_NAME", "")
        out["BEGIN_LOCATION"] = raw.get("BEGIN_LOCATION", "")
        out["BEGIN_DATE"] = begin_parsed.map(lambda d: d.strftime("%Y-%m-%d") if d else "")
        out["BEGIN_TIME"] = begin_parsed.map(lambda d: d.strftime("%H:%M") if d else "")

        local_iso = []
        utc_iso = []
        for d in begin_parsed:
            if d is None:
                local_iso.append("")
                utc_iso.append("")
            else:
                if d.tzinfo is None:
                    d = d.replace(tzinfo=CENTRAL)
                local_iso.append(d.isoformat())
                utc_iso.append(d.astimezone(UTC).isoformat())
        out["event_datetime_local"] = local_iso
        out["event_datetime_utc"] = utc_iso

        passthrough = [
            "EVENT_TYPE", "DEATHS_DIRECT", "INJURIES_DIRECT", "STATE_ABBR", "EPISODE_ID",
            "INJURIES_INDIRECT", "DEATHS_INDIRECT", "SOURCE", "FLOOD_CAUSE",
            "BEGIN_RANGE", "BEGIN_AZIMUTH", "END_RANGE", "END_AZIMUTH", "END_LOCATION",
            "BEGIN_LAT", "BEGIN_LON", "END_LAT", "END_LON", "EVENT_NARRATIVE", "EPISODE_NARRATIVE",
        ]
        for col in passthrough:
            out[col] = raw.get(col, "")

        out["DAMAGE_PROPERTY_NUM"] = raw.get("DAMAGE_PROPERTY", "").map(parse_damage)
        out["DAMAGE_CROPS_NUM"] = raw.get("DAMAGE_CROPS", "").map(parse_damage)
        out["END_DATE"] = end_parsed.map(lambda d: d.strftime("%Y-%m-%d") if d else "")
        out["END_TIME"] = end_parsed.map(lambda d: d.strftime("%H:%M") if d else "")
        out["CITY/CITIES"] = ""
        out["STREET(S)"] = ""
        out["Latitude"] = pd.to_numeric(out["BEGIN_LAT"], errors="coerce")
        out["Longitude"] = pd.to_numeric(out["BEGIN_LON"], errors="coerce")
        out["Bayou/Creek/Stream Name"] = ""

        narrative = out["EVENT_NARRATIVE"].fillna("")
        fallback = out["EPISODE_NARRATIVE"].fillna("")
        cat = [severity_category(a if str(a).strip() else b) for a, b in zip(narrative, fallback)]
        out["severity_category"] = cat
        out["severity_weight"] = [severity_weight(c) for c in cat]

        pieces.append(out[OUTPUT_COLUMNS])

    if not pieces:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    return pd.concat(pieces, ignore_index=True)


def dataframe_to_geojson(df: pd.DataFrame) -> dict[str, Any]:
    features = []
    for _, row in df.iterrows():
        lat = pd.to_numeric(row.get("Latitude"), errors="coerce")
        lon = pd.to_numeric(row.get("Longitude"), errors="coerce")
        if pd.isna(lat) or pd.isna(lon):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        props = {}
        for col, value in row.items():
            if pd.isna(value):
                props[col] = None
            elif isinstance(value, (pd.Timestamp, datetime)):
                props[col] = value.isoformat()
            else:
                props[col] = value.item() if hasattr(value, "item") else value

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            "properties": props,
        })

    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="Existing CSV from the old project")
    ap.add_argument("--start-year", type=int, default=1997)
    ap.add_argument("--end-year", type=int, default=datetime.now().year)
    ap.add_argument("--out-csv", default="docs/data/flash_flood_events.csv")
    ap.add_argument("--out-geojson", default="docs/data/flash_flood_events.geojson")
    args = ap.parse_args()

    if args.input:
        df = pd.read_csv(args.input, low_memory=False)
        out = clean_existing_csv(df)
    else:
        out = build_from_ncei(args.start_year, args.end_year)

    out = out.dropna(subset=["Latitude", "Longitude"], how="any").copy()
    out["Latitude"] = pd.to_numeric(out["Latitude"], errors="coerce")
    out["Longitude"] = pd.to_numeric(out["Longitude"], errors="coerce")
    out = out.dropna(subset=["Latitude", "Longitude"])

    out_csv = Path(args.out_csv)
    out_geojson = Path(args.out_geojson)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_geojson.parent.mkdir(parents=True, exist_ok=True)

    out.to_csv(out_csv, index=False)
    geojson = dataframe_to_geojson(out)
    out_geojson.write_text(json.dumps(geojson, indent=2))

    print(f"[OK] Wrote {len(out):,} events to {out_csv}")
    print(f"[OK] Wrote {len(geojson['features']):,} mapped features to {out_geojson}")


if __name__ == "__main__":
    main()
