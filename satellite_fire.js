// Satellite active-fire observations (NASA FIRMS VIIRS) near the selected point.
// Kept separate from hazard curves, triggers and payouts: these are observations, not probabilities.
globalThis.SatelliteFire = (() => {
  const status = document.getElementById("satellite-fire-status");
  const content = document.getElementById("satellite-fire-content");
  const list = document.getElementById("satellite-fire-list");
  const sources = document.getElementById("satellite-fire-sources");
  const radius = document.getElementById("satellite-fire-radius");
  const refresh = document.getElementById("satellite-fire-refresh");
  const show = document.getElementById("satellite-fire-show");
  const windowSelect = document.getElementById("satellite-fire-window");
  const legend = document.getElementById("satellite-fire-legend");
  const SHOWN = 10;
  // Age colour bands scale with the window so a 7-day view is not almost all one colour.
  const BANDS = {"24h": [6, 12], "48h": [12, 24], "7d": [24, 72]};
  const LABELS = {"24h": "the last 24 hours", "48h": "the last 48 hours", "7d": "the last 7 days"};
  let map = null, selection = null, controller = null, layers = [], ring = null, points = [];

  function bands() {
    return BANDS[windowSelect.value] || BANDS["24h"];
  }

  function hours(value) {
    return value % 24 === 0 && value >= 24 ? `${value / 24} day${value === 24 ? "" : "s"}` : `${value} hours`;
  }

  function updateLegend() {
    const [red, orange] = bands();
    legend.textContent = `Hot spots detected by the NOAA-20 and NOAA-21 satellites in about ${LABELS[windowSelect.value] || LABELS["24h"]}, in ~375 m pixels. ` +
      `Marker colour shows age: red under ${hours(red)}, orange under ${hours(orange)}, amber older.`;
  }

  function color(age) {
    const [red, orange] = bands();
    return age < red ? "#b91c1c" : age < orange ? "#ea580c" : "#d97706";
  }

  function resetResult() {
    for (const layer of layers) map?.removeLayer(layer);
    if (ring) map?.removeLayer(ring);
    layers = []; points = []; ring = null;
    list.replaceChildren();
    sources.textContent = "";
    content.hidden = true;
    show.hidden = true;
  }

  function clear(message = "Select a location to check recent satellite fire detections.") {
    controller?.abort();
    controller = null;
    selection = null;
    resetResult();
    refresh.disabled = true;
    radius.disabled = true;
    windowSelect.disabled = true;
    status.textContent = message;
  }

  function sourceSummary(data) {
    return data.sources.map(source => source.status === "ok"
      ? `${source.satellite}: ${source.file_detection_count} US detections, ${source.oldest_acquired_at || "—"} to ${source.newest_acquired_at || "—"} (UTC)`
      : `${source.satellite}: unavailable`).join(" · ");
  }

  function render(data, point) {
    const where = point.area ? "the area center" : "your point";
    ring = L.circle(point, {radius: data.radius_km * 1000, color: "#b91c1c", weight: 1.5, dashArray: "5 5", fill: false, interactive: false}).addTo(map);
    sources.textContent = `${sourceSummary(data)}. Checked ${data.checked_at}.`;
    if (data.status === "unavailable") {
      status.textContent = data.reason;
      content.hidden = false;
      return;
    }
    const period = LABELS[data.window] || LABELS["24h"];
    if (!data.detections.length) {
      status.textContent = `No satellite fire detections within ${data.radius_km} km of ${where} in about ${period}. This does not mean there is no fire.`;
      if (data.reason && data.reason.startsWith("Partial")) status.textContent += ` ${data.reason}`;
      content.hidden = false;
      return;
    }
    status.textContent = `${data.detection_count} detection${data.detection_count === 1 ? "" : "s"} within ${data.radius_km} km of ${where} in about ${period}. ` +
      `Nearest ${data.nearest_km.toFixed(1)} km; most recent ${data.newest_age_hours.toFixed(1)} hours ago.` +
      (data.reason && data.reason.startsWith("Partial") ? ` ${data.reason}` : "");
    for (const [index, detection] of data.detections.entries()) {
      const at = {lat: detection.latitude, lng: detection.longitude};
      const text = `${detection.distance_km.toFixed(1)} km · ${detection.age_hours.toFixed(1)} h ago (${detection.acquired_at.slice(0, 16).replace("T", " ")} UTC) · ` +
        `${detection.confidence} confidence · ${Number.isFinite(detection.frp_mw) ? `${detection.frp_mw.toFixed(1)} MW` : "FRP not available"} · ` +
        `${detection.satellite} ${detection.daynight || ""}`.trim();
      if (index < SHOWN) {
        const item = document.createElement("li");
        item.textContent = text;
        list.append(item);
      }
      const popup = document.createElement("span");
      popup.textContent = `Satellite detection: ${text}`;
      layers.push(L.circleMarker(at, {radius: 7, color: "#7f1d1d", weight: 1.5, fillColor: color(detection.age_hours), fillOpacity: 0.85}).addTo(map).bindPopup(popup));
      points.push(at);
    }
    if (data.detection_count > SHOWN) {
      const more = document.createElement("li");
      more.textContent = `…and ${data.detection_count - SHOWN} more on the map.`;
      list.append(more);
    }
    content.hidden = false;
    show.hidden = false;
  }

  async function request() {
    if (!selection) return;
    controller?.abort();
    const current = new AbortController(), point = selection;
    controller = current;
    resetResult();
    refresh.disabled = true;
    status.textContent = "Checking NASA satellite fire detections…";
    const timeout = setTimeout(() => current.abort(), 60000);
    try {
      const params = new URLSearchParams({lat: point.lat, lon: point.lng, radius_km: radius.value, window: windowSelect.value});
      const response = await fetch(`/api/v1/satellite-fires?${params}`, {signal: current.signal});
      if (response.status === 404) throw new Error("Restart the Python server to enable satellite fire detections.");
      const payload = await response.json();
      if (controller !== current || selection !== point) return;
      if (!response.ok) throw new Error(payload.error?.message || "Satellite fire lookup failed.");
      const data = payload.data;
      if (!data || !Array.isArray(data.detections) || !Array.isArray(data.sources)) throw new Error("Unexpected satellite fire response.");
      render(data, point);
    } catch (error) {
      if (controller !== current || selection !== point) return;
      resetResult();
      status.textContent = current.signal.aborted
        ? "The satellite fire lookup timed out. Refresh to retry."
        : `Satellite fire detections unavailable: ${error.message}`;
    } finally {
      clearTimeout(timeout);
      if (controller === current) {
        controller = null;
        refresh.disabled = !selection;
      }
    }
  }

  radius.addEventListener("change", request);
  windowSelect.addEventListener("change", () => { updateLegend(); request(); });
  refresh.addEventListener("click", request);
  updateLegend();
  show.addEventListener("click", () => {
    if (selection && points.length) map.fitBounds(L.latLngBounds([selection, ...points]), {padding: [40, 40], maxZoom: 13});
  });

  return {clear, load(leafletMap, point, area = false) {
    clear();
    map = leafletMap;
    selection = {lat: point.lat, lng: point.lng, area};
    radius.disabled = false;
    windowSelect.disabled = false;
    request();
  }};
})();
