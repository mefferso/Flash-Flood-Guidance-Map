// Threshold QC and contour/surface helper for docs/index.html
// Loaded after the main page script. It overrides threshold rendering functions without touching the rest of the app.

const MIN_REALISTIC_3H_THRESHOLD_IN = 1.00;
const MIN_THRESHOLD_SUPPORTING_EVENTS = 2;
const CONTOUR_GRID_DEG = 0.025;
const CONTOUR_SEARCH_RADIUS_DEG = 0.18;
const CONTOUR_POWER = 2;

let thresholdContourLayer = L.layerGroup();
let thresholdQcStats = { shown: 0, hidden: 0, contourCells: 0 };

function ensureThresholdQcControls() {
  const showThreshold = document.getElementById("showThreshold");
  if (!showThreshold || document.getElementById("showThresholdContours")) return;

  const thresholdLabel = showThreshold.closest("label");
  thresholdLabel.insertAdjacentHTML("afterend", `
    <label><input type="checkbox" id="showThresholdContours"> Threshold contour-style surface</label>
    <label><input type="checkbox" id="showSuspectThresholds"> Show suspect/low-confidence thresholds</label>
  `);

  const hint = document.querySelector(".hint");
  if (hint) {
    hint.innerHTML = `Threshold QC hides sub-1.00&quot; 3-hour values and one-event cells by default. Turn on suspect thresholds only for troubleshooting. MRMS-enriched events will automatically show rainfall metrics in popups once <code>mrms_event_metrics.csv</code> is generated and merged.`;
  }

  ["showThresholdContours", "showSuspectThresholds"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => refresh(false));
  });
}

function thresholdColor(val, suspect=false) {
  if (suspect || !Number.isFinite(val)) return "#64748b";
  if (val < 2) return "#991b1b";
  if (val < 3) return "#f97316";
  if (val < 4) return "#facc15";
  if (val < 5) return "#22c55e";
  return "#2563eb";
}

function thresholdInfo(feature) {
  const p = feature.properties || {};
  const threshold = Number(p.threshold_3h_in);
  const eventCount = Number(p.event_count || 0);
  const reasons = [];

  if (!Number.isFinite(threshold)) reasons.push("missing threshold");
  else if (threshold < MIN_REALISTIC_3H_THRESHOLD_IN) reasons.push(`below ${MIN_REALISTIC_3H_THRESHOLD_IN.toFixed(2)} inch QC floor`);

  if (eventCount < MIN_THRESHOLD_SUPPORTING_EVENTS) reasons.push(`fewer than ${MIN_THRESHOLD_SUPPORTING_EVENTS} supporting events`);

  const suspect = reasons.length > 0;
  const confidence = suspect ? "low/suspect" : (eventCount >= 5 ? "high" : eventCount >= 3 ? "medium" : "usable-low");
  return { p, threshold, eventCount, suspect, confidence, qcFlag: suspect ? reasons.join("; ") : "ok" };
}

function getUsableThresholdPoints(includeSuspect=false) {
  return (thresholdGeojson?.features || []).map(f => {
    const pt = getPoint(f);
    if (!pt) return null;
    const info = thresholdInfo(f);
    if (info.suspect && !includeSuspect) return null;
    return { lat: pt[0], lon: pt[1], ...info };
  }).filter(Boolean);
}

function rebuildThreshold() {
  thresholdLayer.clearLayers();

  if (!document.getElementById("showThreshold")?.checked) {
    if (map.hasLayer(thresholdLayer)) map.removeLayer(thresholdLayer);
    thresholdQcStats.shown = 0;
    thresholdQcStats.hidden = 0;
    return 0;
  }

  const showSuspect = document.getElementById("showSuspectThresholds")?.checked;
  let count = 0;
  let hidden = 0;

  (thresholdGeojson?.features || []).forEach(f => {
    const pt = getPoint(f);
    if (!pt) return;

    const [lat, lon] = pt;
    const info = thresholdInfo(f);

    if (info.suspect && !showSuspect) {
      hidden++;
      return;
    }

    const color = thresholdColor(info.threshold, info.suspect);
    const radius = Math.min(16, Math.max(7, 5 + info.eventCount * 2));

    L.circleMarker([lat, lon], {
      radius,
      color: info.suspect ? "#334155" : "#111827",
      weight: info.suspect ? 2 : 1,
      dashArray: info.suspect ? "4 3" : null,
      fillColor: color,
      fillOpacity: info.suspect ? 0.45 : 0.72
    }).bindPopup(`
      <div style="max-width:310px">
        <b>Empirical 3-hr flood threshold</b><br>
        <b>Threshold:</b> ${Number.isFinite(info.threshold) ? info.threshold.toFixed(2) + '&quot;' : "n/a"}<br>
        <b>Supporting events:</b> ${info.eventCount}<br>
        <b>Confidence:</b> ${esc(info.confidence)}<br>
        <b>QC flag:</b> ${esc(info.qcFlag)}<br>
        <hr>
        <span style="font-size:.9em;color:#475569">
          Lower values mean flash flooding has occurred nearby with less 3-hour MRMS QPE.
          Values below 1.00&quot; or based on only one event are hidden by default because they are likely location/QPE/timing artifacts, not reliable operational guidance.
        </span>
      </div>
    `).addTo(thresholdLayer);

    count++;
  });

  thresholdQcStats.shown = count;
  thresholdQcStats.hidden = hidden;

  if (!map.hasLayer(thresholdLayer)) thresholdLayer.addTo(map);
  return count;
}

function rebuildThresholdContours() {
  thresholdContourLayer.clearLayers();

  if (!document.getElementById("showThresholdContours")?.checked) {
    if (map.hasLayer(thresholdContourLayer)) map.removeLayer(thresholdContourLayer);
    thresholdQcStats.contourCells = 0;
    return 0;
  }

  const pts = getUsableThresholdPoints(false);
  if (pts.length < 3) {
    thresholdQcStats.contourCells = 0;
    return 0;
  }

  const lats = pts.map(p => p.lat);
  const lons = pts.map(p => p.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);

  let cells = 0;

  for (let lat = minLat; lat <= maxLat; lat += CONTOUR_GRID_DEG) {
    for (let lon = minLon; lon <= maxLon; lon += CONTOUR_GRID_DEG) {
      let num = 0;
      let den = 0;
      let nearest = Infinity;

      pts.forEach(p => {
        const d = Math.sqrt((lat - p.lat) ** 2 + (lon - p.lon) ** 2);
        nearest = Math.min(nearest, d);
        if (d <= CONTOUR_SEARCH_RADIUS_DEG) {
          const w = 1 / Math.max(0.0001, d ** CONTOUR_POWER);
          num += p.threshold * w;
          den += w;
        }
      });

      if (!den || nearest > CONTOUR_SEARCH_RADIUS_DEG) continue;

      const val = num / den;
      const color = thresholdColor(val, false);

      L.rectangle(
        [[lat - CONTOUR_GRID_DEG / 2, lon - CONTOUR_GRID_DEG / 2], [lat + CONTOUR_GRID_DEG / 2, lon + CONTOUR_GRID_DEG / 2]],
        { stroke: false, fillColor: color, fillOpacity: 0.23, interactive: false }
      ).addTo(thresholdContourLayer);

      cells++;
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
  const hiddenText = thresholdQcStats.hidden ? ` Suspect threshold cells hidden: ${thresholdQcStats.hidden}.` : "";
  const contourText = contourCount ? ` Threshold contour cells shown: ${contourCount}.` : "";

  setStatus(`Mapped ${eventCount} flash flood events. Threshold points shown: ${thresholdCount}.${hiddenText}${contourText} Infrastructure features shown: ${infraCount}. Source: ${eventDataSource}.${skippedText}`);
};

window.addEventListener("load", () => {
  ensureThresholdQcControls();
});
