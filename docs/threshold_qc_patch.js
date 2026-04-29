// Threshold QC and contour/surface helper for docs/index.html
// Loaded after the main page script. It overrides threshold rendering functions without touching the rest of the app.

const MIN_REALISTIC_3H_THRESHOLD_IN = 1.20;
const MIN_THRESHOLD_SUPPORTING_EVENTS = 1;

// Smaller grid = finer display. Larger search radius + Gaussian weighting = smoother field.
const CONTOUR_GRID_DEG = 0.0125;
const CONTOUR_SEARCH_RADIUS_DEG = 0.22;
const CONTOUR_SMOOTHING_SIGMA_DEG = 0.075;
const CONTOUR_MIN_SUPPORT_POINTS = 3;
const CONTOUR_LEVELS = [2, 3, 4];

let thresholdContourLayer = L.layerGroup();
let thresholdQcStats = { shown: 0, flagged: 0, contourCells: 0, contourLabels: 0 };

function ensureThresholdQcControls() {
  const showThreshold = document.getElementById("showThreshold");
  if (!showThreshold || document.getElementById("showThresholdContours")) return;

  const thresholdLabel = showThreshold.closest("label");
  thresholdLabel.insertAdjacentHTML("afterend", `
    <label><input type="checkbox" id="showThresholdContours"> Threshold smoothed surface + labels</label>
  `);

  const hint = document.querySelector(".hint");
  if (hint) {
    hint.innerHTML = `Threshold points are all displayed, but sub-${MIN_REALISTIC_3H_THRESHOLD_IN.toFixed(2)}&quot; / low-confidence values are flagged in popups and excluded from the smoothed contour surface.`;
  }

  const el = document.getElementById("showThresholdContours");
  if (el) el.addEventListener("change", () => refresh(false));
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

function getUsableThresholdPoints(includeFlagged=false) {
  return (thresholdGeojson?.features || []).map(f => {
    const pt = getPoint(f);
    if (!pt) return null;
    const info = thresholdInfo(f);
    if (!Number.isFinite(info.threshold)) return null;
    if (info.suspect && !includeFlagged) return null;
    return { lat: pt[0], lon: pt[1], ...info };
  }).filter(Boolean);
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
          All threshold points are displayed for transparency. Flagged points are kept visible for review, but excluded from the smoothed contour surface so bogus low values do not contaminate the spatial guidance.
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

function contourLineColor(level) {
  if (level <= 2) return "#991b1b";
  if (level <= 3) return "#b45309";
  return "#166534";
}

function addContourLabel(lat, lon, text, color) {
  const icon = L.divIcon({
    className: "",
    html: `<div style="font:bold 11px Arial,sans-serif;color:${color};background:rgba(255,255,255,.78);border:1px solid ${color};border-radius:4px;padding:1px 4px;white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,.18);">${text}</div>`,
    iconSize: [44, 18],
    iconAnchor: [22, 9]
  });
  L.marker([lat, lon], { icon, interactive: false }).addTo(thresholdContourLayer);
  thresholdQcStats.contourLabels++;
}

function drawBoundarySegment(lat1, lon1, lat2, lon2, level, labelEveryN, counterObj) {
  const color = contourLineColor(level);
  L.polyline([[lat1, lon1], [lat2, lon2]], {
    color,
    weight: 2,
    opacity: 0.75,
    interactive: false
  }).addTo(thresholdContourLayer);

  counterObj.count++;
  if (counterObj.count % labelEveryN === 0) {
    addContourLabel((lat1 + lat2) / 2, (lon1 + lon2) / 2, `${level}\"`, color);
  }
}

function rebuildThresholdContours() {
  thresholdContourLayer.clearLayers();
  thresholdQcStats.contourCells = 0;
  thresholdQcStats.contourLabels = 0;

  if (!document.getElementById("showThresholdContours")?.checked) {
    if (map.hasLayer(thresholdContourLayer)) map.removeLayer(thresholdContourLayer);
    return 0;
  }

  // Use only non-flagged points for the contour/surface so 0.19" junk stays visible as a point but does not smear into the guidance field.
  const pts = getUsableThresholdPoints(false);
  if (pts.length < 3) return 0;

  const lats = pts.map(p => p.lat);
  const lons = pts.map(p => p.lon);
  const minLat = Math.min(...lats) - CONTOUR_GRID_DEG;
  const maxLat = Math.max(...lats) + CONTOUR_GRID_DEG;
  const minLon = Math.min(...lons) - CONTOUR_GRID_DEG;
  const maxLon = Math.max(...lons) + CONTOUR_GRID_DEG;

  const rows = [];
  const latVals = [];
  const lonVals = [];

  for (let lat = minLat; lat <= maxLat + 1e-9; lat += CONTOUR_GRID_DEG) latVals.push(lat);
  for (let lon = minLon; lon <= maxLon + 1e-9; lon += CONTOUR_GRID_DEG) lonVals.push(lon);

  for (let i = 0; i < latVals.length; i++) {
    const lat = latVals[i];
    rows[i] = [];
    for (let j = 0; j < lonVals.length; j++) {
      const lon = lonVals[j];
      let num = 0;
      let den = 0;
      let support = 0;
      let nearest = Infinity;

      pts.forEach(p => {
        const d = Math.sqrt((lat - p.lat) ** 2 + (lon - p.lon) ** 2);
        nearest = Math.min(nearest, d);
        if (d <= CONTOUR_SEARCH_RADIUS_DEG) {
          const w = Math.exp(-(d * d) / (2 * CONTOUR_SMOOTHING_SIGMA_DEG * CONTOUR_SMOOTHING_SIGMA_DEG));
          num += p.threshold * w;
          den += w;
          support++;
        }
      });

      if (!den || support < CONTOUR_MIN_SUPPORT_POINTS || nearest > CONTOUR_SEARCH_RADIUS_DEG) {
        rows[i][j] = null;
        continue;
      }

      rows[i][j] = num / den;
    }
  }

  let cells = 0;
  const lineCounters = {};
  CONTOUR_LEVELS.forEach(level => lineCounters[level] = { count: 0 });

  for (let i = 0; i < latVals.length - 1; i++) {
    for (let j = 0; j < lonVals.length - 1; j++) {
      const v00 = rows[i][j];
      const v01 = rows[i][j + 1];
      const v10 = rows[i + 1][j];
      const v11 = rows[i + 1][j + 1];
      if ([v00, v01, v10, v11].some(v => v === null)) continue;

      const avg = (v00 + v01 + v10 + v11) / 4;
      const color = thresholdColor(avg, false);
      const lat0 = latVals[i];
      const lat1 = latVals[i + 1];
      const lon0 = lonVals[j];
      const lon1 = lonVals[j + 1];

      L.rectangle([[lat0, lon0], [lat1, lon1]], {
        stroke: false,
        fillColor: color,
        fillOpacity: 0.18,
        interactive: false
      }).addTo(thresholdContourLayer);
      cells++;

      CONTOUR_LEVELS.forEach(level => {
        const thisBin = avg >= level;
        const rightVal = j + 1 < lonVals.length - 1 ? rows[i][j + 2] : null;
        const upVal = i + 1 < latVals.length - 1 ? rows[i + 2][j] : null;

        if (rightVal !== null) {
          const rightAvg = (v01 + rightVal + v11 + rows[i + 1][j + 2]) / 4;
          if ((avg < level && rightAvg >= level) || (avg >= level && rightAvg < level)) {
            drawBoundarySegment(lat0, lon1, lat1, lon1, level, 18, lineCounters[level]);
          }
        }

        if (upVal !== null) {
          const upAvg = (v10 + v11 + upVal + rows[i + 2][j + 1]) / 4;
          if ((avg < level && upAvg >= level) || (avg >= level && upAvg < level)) {
            drawBoundarySegment(lat1, lon0, lat1, lon1, level, 18, lineCounters[level]);
          }
        }
      });
    }
  }

  thresholdQcStats.contourCells = cells;
  if (!map.hasLayer(thresholdContourLayer)) thresholdContourLayer.addTo(map);
  return cells;
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
  const contourText = contourCount ? ` Smoothed contour cells shown: ${contourCount}. Contour labels: ${thresholdQcStats.contourLabels}.` : "";

  setStatus(`Mapped ${eventCount} flash flood events. Threshold points shown: ${thresholdCount}.${flaggedText}${contourText} Infrastructure features shown: ${infraCount}. Source: ${eventDataSource}.${skippedText}`);
};

window.addEventListener("load", () => {
  ensureThresholdQcControls();
});
