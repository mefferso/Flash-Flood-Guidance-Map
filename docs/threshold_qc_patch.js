// Threshold QC and generated contour helper for docs/index.html
// Loaded after the main page script. It overrides threshold rendering functions without touching the rest of the app.

const MIN_REALISTIC_3H_THRESHOLD_IN = 1.20;
const MIN_THRESHOLD_SUPPORTING_EVENTS = 1;
const THRESHOLD_CONTOURS_URL = "data/threshold_contours.geojson";
const THRESHOLD_CONTOUR_LINES_URL = "data/threshold_contour_lines.geojson";

let thresholdContourLayer = L.layerGroup();
let thresholdContourGeojson = null;
let thresholdContourLinesGeojson = null;
let thresholdContourLoadAttempted = false;
let thresholdQcStats = { shown: 0, flagged: 0, contourFeatures: 0, contourLineFeatures: 0, contourLabels: 0 };

function ensureThresholdQcControls() {
  const showThreshold = document.getElementById("showThreshold");
  if (!showThreshold || document.getElementById("showThresholdContours")) return;

  const thresholdLabel = showThreshold.closest("label");
  thresholdLabel.insertAdjacentHTML("afterend", `
    <label><input type="checkbox" id="showThresholdContours"> Threshold contours</label>
  `);

  const hint = document.querySelector(".hint");
  if (hint) {
    hint.innerHTML = `Threshold points are all displayed. Sub-${MIN_REALISTIC_3H_THRESHOLD_IN.toFixed(2)}&quot; / low-confidence values are flagged in popups, while the contour layer uses generated GeoJSON built from non-flagged points.`;
  }

  const el = document.getElementById("showThresholdContours");
  if (el) el.addEventListener("change", () => refresh(false));
}

async function fetchGeoJSONStrict(url) {
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} while loading ${url}`);
  const json = await resp.json();
  if (!json || json.type !== "FeatureCollection" || !Array.isArray(json.features)) {
    throw new Error(`Invalid GeoJSON from ${url}`);
  }
  return json;
}

async function loadGeneratedThresholdContours() {
  if (thresholdContourLoadAttempted) return;
  thresholdContourLoadAttempted = true;

  try {
    thresholdContourGeojson = await fetchGeoJSONStrict(THRESHOLD_CONTOURS_URL);
  } catch (err) {
    console.warn("Threshold filled contours unavailable:", err);
    thresholdContourGeojson = { type: "FeatureCollection", features: [] };
  }

  try {
    thresholdContourLinesGeojson = await fetchGeoJSONStrict(THRESHOLD_CONTOUR_LINES_URL);
  } catch (err) {
    console.warn("Threshold contour lines unavailable:", err);
    thresholdContourLinesGeojson = { type: "FeatureCollection", features: [] };
  }
}

function thresholdColor(val, suspect=false) {
  if (!Number.isFinite(val)) return "#64748b";
  if (val < 2) return "#dc2626";
  if (val < 3) return "#f97316";
  if (val < 4) return "#facc15";
  return "#22c55e";
}

function thresholdInfo(feature) {
  const p = feature.properties || {};
  const threshold = Number(p.threshold_3h_in);
  const eventCount = Number(p.event_count || 0);
  const reasons = [];

  if (!Number.isFinite(threshold)) reasons.push("missing threshold");
  else if (threshold < MIN_REALISTIC_3H_THRESHOLD_IN) reasons.push(`below ${MIN_REALISTIC_3H_THRESHOLD_IN.toFixed(2)} inch QC flag`);

  if (eventCount < MIN_THRESHOLD_SUPPORTING_EVENTS) reasons.push(`fewer than ${MIN_THRESHOLD_SUPPORTING_EVENTS} supporting events`);

  const suspect = reasons.length > 0;
  const confidence = suspect ? "flagged/low confidence" : (eventCount >= 5 ? "high" : eventCount >= 3 ? "medium" : "usable-low");
  return { p, threshold, eventCount, suspect, confidence, qcFlag: suspect ? reasons.join("; ") : "ok" };
}

function rebuildThreshold() {
  thresholdLayer.clearLayers();

  if (!document.getElementById("showThreshold")?.checked) {
    if (map.hasLayer(thresholdLayer)) map.removeLayer(thresholdLayer);
    thresholdQcStats.shown = 0;
    thresholdQcStats.flagged = 0;
    return 0;
  }

  let count = 0;
  let flagged = 0;

  (thresholdGeojson?.features || []).forEach(f => {
    const pt = getPoint(f);
    if (!pt) return;

    const [lat, lon] = pt;
    const info = thresholdInfo(f);
    if (info.suspect) flagged++;

    const color = thresholdColor(info.threshold, info.suspect);
    const radius = Math.min(16, Math.max(7, 5 + info.eventCount * 2));

    L.circleMarker([lat, lon], {
      radius,
      color: "#111827",
      weight: info.suspect ? 2 : 1,
      dashArray: info.suspect ? "4 3" : null,
      fillColor: color,
      fillOpacity: 0.72
    }).bindPopup(`
      <div style="max-width:310px">
        <b>Empirical 3-hr flood threshold</b><br>
        <b>Threshold:</b> ${Number.isFinite(info.threshold) ? info.threshold.toFixed(2) + '&quot;' : "n/a"}<br>
        <b>Supporting events:</b> ${info.eventCount}<br>
        <b>Confidence:</b> ${esc(info.confidence)}<br>
        <b>QC flag:</b> ${esc(info.qcFlag)}<br>
        <hr>
        <span style="font-size:.9em;color:#475569">
          All threshold points are displayed for transparency. Flagged points are kept visible for review, but are excluded from the generated contour surface so bogus low values do not contaminate the spatial guidance.
        </span>
      </div>
    `).addTo(thresholdLayer);

    count++;
  });

  thresholdQcStats.shown = count;
  thresholdQcStats.flagged = flagged;

  if (!map.hasLayer(thresholdLayer)) thresholdLayer.addTo(map);
  return count;
}

function styleFilledContour(feature) {
  const p = feature.properties || {};
  return {
    color: "transparent",
    weight: 0,
    fillColor: p.fill || thresholdColor(Number(p.high_in || p.low_in), false),
    fillOpacity: 0.24,
    interactive: false
  };
}

function styleContourLine(feature) {
  const p = feature.properties || {};
  const level = Number(p.level_in);
  return {
    color: p.stroke || (level >= 4 ? "#166534" : "#7c2d12"),
    weight: level >= 4 ? 2.4 : 2.0,
    opacity: 0.85,
    interactive: false
  };
}

function addLineLabels(lineGeojson) {
  thresholdQcStats.contourLabels = 0;
  const labelCounters = {};

  (lineGeojson?.features || []).forEach(feature => {
    const p = feature.properties || {};
    const level = Number(p.level_in);
    const label = p.label || (Number.isFinite(level) ? `${level}"` : "");
    const color = p.stroke || (level >= 4 ? "#166534" : "#7c2d12");
    const geom = feature.geometry || {};
    if (geom.type !== "LineString" || !Array.isArray(geom.coordinates) || geom.coordinates.length < 2) return;

    const key = String(label);
    labelCounters[key] = (labelCounters[key] || 0) + 1;

    // Label roughly every 3rd line segment feature to avoid sticker-bombing the map.
    if (labelCounters[key] % 3 !== 1) return;

    const midIdx = Math.floor(geom.coordinates.length / 2);
    const coord = geom.coordinates[midIdx];
    if (!coord || coord.length < 2) return;

    const icon = L.divIcon({
      className: "",
      html: `<div style="font:bold 11px Arial,sans-serif;color:${color};background:rgba(255,255,255,.82);border:1px solid ${color};border-radius:4px;padding:1px 4px;white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,.20);">${esc(label)}</div>`,
      iconSize: [44, 18],
      iconAnchor: [22, 9]
    });

    L.marker([coord[1], coord[0]], { icon, interactive: false }).addTo(thresholdContourLayer);
    thresholdQcStats.contourLabels++;
  });
}

function rebuildThresholdContours() {
  thresholdContourLayer.clearLayers();
  thresholdQcStats.contourFeatures = 0;
  thresholdQcStats.contourLineFeatures = 0;
  thresholdQcStats.contourLabels = 0;

  if (!document.getElementById("showThresholdContours")?.checked) {
    if (map.hasLayer(thresholdContourLayer)) map.removeLayer(thresholdContourLayer);
    return 0;
  }

  const filled = thresholdContourGeojson || { type: "FeatureCollection", features: [] };
  const lines = thresholdContourLinesGeojson || { type: "FeatureCollection", features: [] };

  if (filled.features.length) {
    L.geoJSON(filled, { style: styleFilledContour, interactive: false }).addTo(thresholdContourLayer);
  }

  if (lines.features.length) {
    L.geoJSON(lines, { style: styleContourLine, interactive: false }).addTo(thresholdContourLayer);
    addLineLabels(lines);
  }

  thresholdQcStats.contourFeatures = filled.features.length;
  thresholdQcStats.contourLineFeatures = lines.features.length;

  if (!map.hasLayer(thresholdContourLayer)) thresholdContourLayer.addTo(map);
  return filled.features.length + lines.features.length;
}

const originalRefresh = refresh;
refresh = function(fit=false) {
  ensureThresholdQcControls();

  const features = getFilteredEvents();

  if (document.getElementById("showEvents").checked) {
    if (!map.hasLayer(eventLayer)) eventLayer.addTo(map);
  } else if (map.hasLayer(eventLayer)) {
    map.removeLayer(eventLayer);
  }

  const eventCount = rebuildEvents(features, fit);
  rebuildHeat(features);

  const thresholdIsOn = document.getElementById("showThreshold")?.checked || document.getElementById("showThresholdContours")?.checked;
  if (thresholdIsOn) {
    if (heatLayer) {
      map.removeLayer(heatLayer);
      heatLayer = null;
    }
    eventLayer.eachLayer(l => {
      if (l.setStyle) l.setStyle({ fillOpacity: 0.3 });
    });
  }

  const contourCount = rebuildThresholdContours();
  const thresholdCount = rebuildThreshold();
  const infraCount = rebuildInfra();
  const skippedText = skippedEventRows ? ` Skipped ${skippedEventRows} CSV rows with missing/bad coordinates.` : "";
  const flaggedText = thresholdQcStats.flagged ? ` Flagged threshold points shown: ${thresholdQcStats.flagged}.` : "";
  const contourText = contourCount ? ` Generated contour features shown: ${thresholdQcStats.contourFeatures}; lines: ${thresholdQcStats.contourLineFeatures}; labels: ${thresholdQcStats.contourLabels}.` : "";

  setStatus(`Mapped ${eventCount} flash flood events. Threshold points shown: ${thresholdCount}.${flaggedText}${contourText} Infrastructure features shown: ${infraCount}. Source: ${eventDataSource}.${skippedText}`);
};

const originalLoadData = loadData;
loadData = async function() {
  setStatus("Loading event data...");
  eventGeojson = await loadEventData();

  setStatus("Loading optional threshold, contour, and infrastructure layers...");
  thresholdGeojson = await fetchGeoJSONOrEmpty(THRESHOLD_URL);
  await loadGeneratedThresholdContours();
  infraGeojson = await fetchGeoJSONOrEmpty(INFRA_URL);

  refresh(true);
};

window.addEventListener("load", () => {
  ensureThresholdQcControls();
});
