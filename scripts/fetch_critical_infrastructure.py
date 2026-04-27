#!/usr/bin/env python3
"""
Fetch public critical infrastructure layers for the map.

Default source:
HIFLD / ArcGIS Critical Infrastructure Map Service
https://services.arcgis.com/XG15cJAlne2vxtgt/ArcGIS/rest/services/Critical_Infrastructure_Map_Service/FeatureServer

This script queries only features intersecting the LIX bbox so the browser does not get fed a nationwide GI tract blockage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests

from config import LIX_BBOX

SERVICE = "https://services.arcgis.com/XG15cJAlne2vxtgt/ArcGIS/rest/services/Critical_Infrastructure_Map_Service/FeatureServer"

# Layer IDs from the service.
LAYERS = {
    0: "shelter",
    1: "public_school",
    2: "private_school",
    3: "college_university",
    5: "power_generation",
    8: "wastewater",
    9: "nursing_home",
    10: "hospital",
    11: "law_enforcement",
    12: "fire_station",
    13: "eoc",
    14: "ems_station",
}


def arcgis_query(layer_id: int, bbox: tuple[float, float, float, float], result_offset: int = 0) -> dict[str, Any]:
    xmin, ymin, xmax, ymax = bbox
    url = f"{SERVICE}/{layer_id}/query"
    params = {
        "f": "json",
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "geometry": json.dumps({
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
            "spatialReference": {"wkid": 4326},
        }),
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": 4326,
        "outSR": 4326,
        "resultOffset": result_offset,
        "resultRecordCount": 1000,
    }
    r = requests.get(url, params=params, timeout=90)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error for layer {layer_id}: {data['error']}")
    return data


def arcgis_feature_to_geojson(feature: dict[str, Any], infra_type: str, layer_id: int) -> dict[str, Any] | None:
    geom = feature.get("geometry") or {}
    attrs = feature.get("attributes") or {}

    x = geom.get("x")
    y = geom.get("y")

    # Some layers may return polygons/lines. Keep points, skip non-points for now.
    # You can add polygon handling later if needed.
    if x is None or y is None:
        return None

    attrs = dict(attrs)
    attrs["infra_type"] = infra_type
    attrs["source_layer_id"] = layer_id

    # Try to create a generic display name for popups.
    for key in ("NAME", "Name", "name", "FACILITY", "FACILITY_NAME", "SCHOOL_NAME", "HOSPITAL_NAME"):
        if key in attrs and attrs[key]:
            attrs["display_name"] = attrs[key]
            break
    else:
        attrs["display_name"] = infra_type.replace("_", " ").title()

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(x), float(y)]},
        "properties": attrs,
    }


def fetch_all_layers(bbox: tuple[float, float, float, float], layers: dict[int, str]) -> dict[str, Any]:
    features = []

    for layer_id, infra_type in layers.items():
        print(f"[INFO] Fetching {infra_type} layer {layer_id}")
        offset = 0
        while True:
            data = arcgis_query(layer_id, bbox, result_offset=offset)
            raw_features = data.get("features", [])
            for f in raw_features:
                gj = arcgis_feature_to_geojson(f, infra_type, layer_id)
                if gj:
                    features.append(gj)

            exceeded = bool(data.get("exceededTransferLimit"))
            if not exceeded or not raw_features:
                break
            offset += len(raw_features)

    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default=",".join(map(str, LIX_BBOX)), help="xmin,ymin,xmax,ymax")
    ap.add_argument("--out", default="docs/data/critical_infrastructure.geojson")
    args = ap.parse_args()

    bbox = tuple(float(x.strip()) for x in args.bbox.split(","))
    if len(bbox) != 4:
        raise ValueError("--bbox must be xmin,ymin,xmax,ymax")

    geojson = fetch_all_layers(bbox, LAYERS)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(geojson, indent=2))
    print(f"[OK] Wrote {len(geojson['features']):,} infrastructure features to {out}")


if __name__ == "__main__":
    main()
