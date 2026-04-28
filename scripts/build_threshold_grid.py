import pandas as pd
import numpy as np
import json

INPUT = "docs/data/mrms_sampled.csv"
OUTPUT = "docs/data/threshold_grid.geojson"

# grid size in degrees (~0.05 ≈ ~3-4 miles)
GRID_SIZE = 0.05

df = pd.read_csv(INPUT)

# convert mm → inches
df["max_3h_in"] = df["max_3h_qpe_mm"] / 25.4

# drop missing
df = df.dropna(subset=["max_3h_in"])

# snap to grid
df["grid_lat"] = (df["Latitude"] / GRID_SIZE).round() * GRID_SIZE
df["grid_lon"] = (df["Longitude"] / GRID_SIZE).round() * GRID_SIZE

# aggregate
grouped = df.groupby(["grid_lat", "grid_lon"]).agg({
    "max_3h_in": "median",
    "EVENT_ID": "count"
}).reset_index()

features = []

for _, row in grouped.iterrows():
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [row["grid_lon"], row["grid_lat"]]
        },
        "properties": {
            "threshold_3h_in": row["max_3h_in"],
            "event_count": int(row["EVENT_ID"])
        }
    })

geojson = {
    "type": "FeatureCollection",
    "features": features
}

with open(OUTPUT, "w") as f:
    json.dump(geojson, f)

print(f"Wrote {len(features)} grid points")
