#!/usr/bin/env python3
"""
Build real threshold contour GeoJSON files from docs/data/threshold_grid.geojson.

Outputs:
  docs/data/threshold_contours.geojson
  docs/data/threshold_contour_lines.geojson

This intentionally excludes flagged/suspect low threshold values from the interpolated
surface while leaving the original point layer untouched for review/transparency.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

try:
    from scipy.interpolate import griddata
    from scipy.ndimage import gaussian_filter
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency. Install scipy first: python -m pip install scipy matplotlib numpy"
    ) from exc

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency. Install matplotlib first: python -m pip install matplotlib scipy numpy"
    ) from exc

INPUT = Path("docs/data/threshold_grid.geojson")
OUT_FILLED = Path("docs/data/threshold_contours.geojson")
OUT_LINES = Path("docs/data/threshold_contour_lines.geojson")

MIN_REALISTIC_3H_THRESHOLD_IN = 1.20
MIN_SUPPORTING_EVENTS_FOR_SURFACE = 1

GRID_SPACING_DEG = 0.00625
GAUSSIAN_SIGMA_CELLS = 2.0

FILLED_LEVELS = [1.2, 2.0, 3.0, 4.0, 5.0, 8.0]
LINE_LEVELS = [2.0, 3.0, 4.0, 5.0]
MAX_DISTANCE_DEG = 0.20


def load_points() -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[dict]]:
    if not INPUT.exists():
        raise SystemExit(f"Missing input file: {INPUT}")

    data = json.loads(INPUT.read_text())
    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    used_features: List[dict] = []

    for feature in data.get("features", []):
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") != "Point" or len(coords) < 2:
            continue

        lon, lat = float(coords[0]), float(coords[1])
        try:
            val = float(props.get("threshold_3h_in"))
        except (TypeError, ValueError):
            continue
        try:
            event_count = int(float(props.get("event_count", 0)))
        except (TypeError, ValueError):
            event_count = 0

        if not math.isfinite(val) or val < MIN_REALISTIC_3H_THRESHOLD_IN:
            continue
        if event_count < MIN_SUPPORTING_EVENTS_FOR_SURFACE:
            continue

        xs.append(lon)
        ys.append(lat)
        zs.append(val)
        used_features.append(feature)

    if len(xs) < 3:
        raise SystemExit("Not enough usable threshold points to contour.")

    return np.asarray(xs), np.asarray(ys), np.asarray(zs), used_features


def distance_mask(grid_x: np.ndarray, grid_y: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    mask = np.zeros(grid_x.shape, dtype=bool)
    for idx in np.ndindex(grid_x.shape):
        gx = grid_x[idx]
        gy = grid_y[idx]
        d2 = np.min((xs - gx) ** 2 + (ys - gy) ** 2)
        mask[idx] = math.sqrt(float(d2)) <= MAX_DISTANCE_DEG
    return mask


def build_grid(xs: np.ndarray, ys: np.ndarray, zs: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    pad = 0.05
    lon_min, lon_max = xs.min() - pad, xs.max() + pad
    lat_min, lat_max = ys.min() - pad, ys.max() + pad

    grid_lons = np.arange(lon_min, lon_max + GRID_SPACING_DEG, GRID_SPACING_DEG)
    grid_lats = np.arange(lat_min, lat_max + GRID_SPACING_DEG, GRID_SPACING_DEG)
    grid_x, grid_y = np.meshgrid(grid_lons, grid_lats)

    linear = griddata((xs, ys), zs, (grid_x, grid_y), method="linear")
    nearest = griddata((xs, ys), zs, (grid_x, grid_y), method="nearest")
    surface = np.where(np.isnan(linear), nearest, linear)

    valid_distance = distance_mask(grid_x, grid_y, xs, ys)
    surface = np.where(valid_distance, surface, np.nan)

    valid = np.isfinite(surface).astype(float)
    filled = np.where(np.isfinite(surface), surface, 0.0)
    smooth_num = gaussian_filter(filled, sigma=GAUSSIAN_SIGMA_CELLS)
    smooth_den = gaussian_filter(valid, sigma=GAUSSIAN_SIGMA_CELLS)

    with np.errstate(invalid="ignore", divide="ignore"):
        smooth = smooth_num / smooth_den

    smooth = np.where((smooth_den > 0.20) & valid_distance, smooth, np.nan)
    return grid_x, grid_y, smooth


def polygon_feature(coords: List[List[float]], props: Dict[str, object]) -> dict:
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords]},
        "properties": props,
    }


def line_feature(coords: List[List[float]], props: Dict[str, object]) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": props,
    }


def color_for_bin(low: float, high: float) -> str:
    if high <= 2.0:
        return "#dc2626"
    if high <= 3.0:
        return "#f97316"
    if high <= 4.0:
        return "#facc15"
    if high <= 5.0:
        return "#22c55e"
    return "#2563eb"


def iter_contour_paths(contour_set):
    """Support both older Matplotlib .collections and newer .get_paths APIs."""
    if hasattr(contour_set, "collections"):
        for level_idx, collection in enumerate(contour_set.collections):
            for path in collection.get_paths():
                yield level_idx, path
    else:
        # Matplotlib 3.10+ QuadContourSet exposes all_paths().
        for level_idx, paths in enumerate(contour_set.allsegs):
            for vertices in paths:
                if len(vertices) < 2:
                    continue
                from matplotlib.path import Path as MplPath
                yield level_idx, MplPath(vertices)


def contours_to_geojson(grid_x: np.ndarray, grid_y: np.ndarray, surface: np.ndarray) -> Tuple[dict, dict]:
    filled_features: List[dict] = []
    line_features: List[dict] = []

    fig, ax = plt.subplots(figsize=(6, 6))

    cf = ax.contourf(grid_x, grid_y, surface, levels=FILLED_LEVELS)
    for level_idx, path in iter_contour_paths(cf):
        if level_idx >= len(FILLED_LEVELS) - 1:
            continue
        low = float(FILLED_LEVELS[level_idx])
        high = float(FILLED_LEVELS[level_idx + 1])
        for poly in path.to_polygons():
            if len(poly) < 4:
                continue
            coords = [[float(x), float(y)] for x, y in poly]
            filled_features.append(polygon_feature(coords, {
                "low_in": low,
                "high_in": high,
                "label": f"{low:g}–{high:g}\"",
                "fill": color_for_bin(low, high),
                "source": "python_smoothed_threshold_surface",
            }))

    cs = ax.contour(grid_x, grid_y, surface, levels=LINE_LEVELS)
    for level_idx, path in iter_contour_paths(cs):
        if level_idx >= len(LINE_LEVELS):
            continue
        level = float(LINE_LEVELS[level_idx])
        vertices = path.vertices
        if len(vertices) < 2:
            continue
        coords = [[float(x), float(y)] for x, y in vertices]
        line_features.append(line_feature(coords, {
            "level_in": level,
            "label": f"{level:g}\"",
            "stroke": "#7c2d12" if level < 4 else "#166534",
            "source": "python_smoothed_threshold_contour_line",
        }))

    plt.close(fig)

    return (
        {"type": "FeatureCollection", "features": filled_features},
        {"type": "FeatureCollection", "features": line_features},
    )


def main() -> None:
    xs, ys, zs, used_features = load_points()
    grid_x, grid_y, surface = build_grid(xs, ys, zs)
    filled, lines = contours_to_geojson(grid_x, grid_y, surface)

    OUT_FILLED.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILLED.write_text(json.dumps(filled, separators=(",", ":")))
    OUT_LINES.write_text(json.dumps(lines, separators=(",", ":")))

    print(f"Used threshold points: {len(used_features)}")
    print(f"Wrote filled contours: {OUT_FILLED} ({len(filled['features'])} features)")
    print(f"Wrote contour lines: {OUT_LINES} ({len(lines['features'])} features)")


if __name__ == "__main__":
    main()
