import json, pandas as pd

EVENTS = "docs/data/flash_flood_events.geojson"
MRMS   = "docs/data/mrms_sampled.csv"   # or mrms_event_metrics.csv
OUT    = "docs/data/flash_flood_events_enriched.geojson"

gj = json.load(open(EVENTS))
df = pd.read_csv(MRMS)

# ensure types match
df["EVENT_ID"] = df["EVENT_ID"].astype(int)

# quick lookup
lookup = df.set_index("EVENT_ID").to_dict(orient="index")

count = 0
for f in gj["features"]:
    eid = f["properties"].get("EVENT_ID")
    if eid in lookup:
        m = lookup[eid]
        # attach what you care about
        f["properties"]["max_1h_qpe_mm"] = m.get("max_1h_qpe_mm")
        f["properties"]["max_3h_qpe_mm"] = m.get("max_3h_qpe_mm")
        f["properties"]["max_6h_qpe_mm"] = m.get("max_6h_qpe_mm")
        f["properties"]["max_12h_qpe_mm"] = m.get("max_12h_qpe_mm")
        f["properties"]["mrms_hours_sampled"] = m.get("mrms_hours_sampled")
        count += 1

json.dump(gj, open(OUT, "w"))
print(f"Merged MRMS into {count} events → {OUT}")
