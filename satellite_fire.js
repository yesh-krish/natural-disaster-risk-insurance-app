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
  const frpPanel = document.getElementById("satellite-fire-frp");
  const frpSummary = document.getElementById("satellite-fire-frp-summary");
  const frpTime = document.getElementById("satellite-fire-frp-time");
  const frpCurve = document.getElementById("satellite-fire-frp-curve");
  const frpBasis = document.getElementById("satellite-fire-frp-basis");
  const WINDOW_HOURS = {"24h": 24, "48h": 48, "7d": 168};
  const SHOWN = 10;
  // Age colour bands scale with the window so a 7-day view is not almost all one colour.
  const BANDS = {"24h": [6, 12], "48h": [12, 24], "7d": [24, 72]};
  // NASA's rolling files hold whole UTC days, so each reaches back further than its name
  // (checked 2026-11-22: "24h" ≈ 46 h, "48h" ≈ 71 h, "7d" ≈ 196 h).
  const LABELS = {"24h": "the last 1–2 days", "48h": "the last 2–3 days", "7d": "the last 7–8 days"};
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
    frpPanel.hidden = true;
    frpSummary.textContent = frpBasis.textContent = "";
    frpTime.innerHTML = frpCurve.innerHTML = "";
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

  const mw = value => `${value < 10 ? value.toFixed(2) : value.toFixed(1)} MW`;
  const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

  // FRP (MW) against hours before the check, most recent at the right. Log scale when FRP spans 20× or more.
  function frpTimeSVG(detections, windowHours) {
    const shown = detections.filter(d => Number.isFinite(d.frp_mw) && d.frp_mw >= 0 && Number.isFinite(d.age_hours));
    if (!shown.length) return null;
    const width = 620, height = 300, left = 76, right = 28, top = 18, bottom = 58;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const span = Math.max(windowHours, ...shown.map(d => d.age_hours));
    const values = shown.map(d => d.frp_mw), low = Math.min(...values), high = Math.max(...values);
    const logarithmic = low > 0 && high / low >= 20;
    const lo = logarithmic ? Math.floor(Math.log10(low)) : 0;
    const hi = logarithmic ? Math.ceil(Math.log10(high)) : Math.max(0.1, high * 1.12);
    const y = v => top + (1 - ((logarithmic ? Math.log10(v) : v) - lo) / (hi - lo)) * plotHeight;
    const x = age => left + (1 - age / span) * plotWidth;
    let content = "";
    const ticks = logarithmic ? Array.from({length: hi - lo + 1}, (_, i) => 10 ** (lo + i)) : [0, 1, 2, 3, 4].map(i => hi * i / 4);
    for (const tick of ticks) {
      content += `<line x1="${left}" y1="${y(tick)}" x2="${width - right}" y2="${y(tick)}" stroke="#e2e8f0"/>` +
        `<text x="${left - 10}" y="${y(tick) + 4}" text-anchor="end" font-size="11">${formatHazardNumber(tick)}</text>`;
    }
    for (let i = 0; i <= 4; i++) {
      content += `<text x="${left + plotWidth * i / 4}" y="${height - bottom + 23}" text-anchor="middle" font-size="11">${formatHazardNumber(span * (4 - i) / 4)} h</text>`;
    }
    content += `<line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" stroke="#94a3b8"/>` +
      `<line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" stroke="#94a3b8"/>`;
    for (const d of shown) {
      content += `<circle cx="${x(d.age_hours)}" cy="${y(d.frp_mw)}" r="4.5" fill="${color(d.age_hours)}" fill-opacity="0.85" stroke="#7f1d1d">` +
        `<title>${escape(`${mw(d.frp_mw)} · ${d.age_hours.toFixed(1)} h ago · ${d.distance_km.toFixed(1)} km · ${d.satellite}`)}</title></circle>`;
    }
    const xLabel = "Hours before check (most recent at right)", yLabel = `FRP (MW)${logarithmic ? " · log scale" : ""}`;
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Fire radiative power in MW by detection time">${content}` +
      `<text x="${left + plotWidth / 2}" y="${height - 9}" text-anchor="middle" font-size="12">${xLabel}</text>` +
      `<text transform="translate(17 ${top + plotHeight / 2}) rotate(-90)" text-anchor="middle" font-size="12">${yLabel}</text></svg>`;
  }

  function renderFrp(data) {
    const summary = data.frp_summary;
    if (!summary || !summary.detections_with_frp || !Array.isArray(summary.exceedance)) return;
    const missing = summary.detections_without_frp ? ` ${summary.detections_without_frp} without an FRP value are left out.` : "";
    frpSummary.textContent = `Fire radiative power of ${summary.detections_with_frp} detection${summary.detections_with_frp === 1 ? "" : "s"}: ` +
      `minimum ${mw(summary.minimum_mw)}, median ${mw(summary.median_mw)}, maximum ${mw(summary.maximum_mw)}.${missing}`;
    const plotted = data.detections.filter(d => Number.isFinite(d.frp_mw)).length;
    const timeSvg = frpTimeSVG(data.detections, WINDOW_HOURS[data.window] || 24);
    frpTime.innerHTML = timeSvg || "<p>No returned detection has an FRP value.</p>";
    if (data.detection_count > data.returned_count) {
      frpTime.innerHTML += `<p>Shows the ${plotted} nearest detections with FRP; the summary and exceedance curve use all ${summary.detections_with_frp}.</p>`;
    }
    const curve = hazardChartSVG(summary.exceedance.map(p => ({severity: p.frp_mw, frequency_pct: p.share_pct})),
      "Fire radiative power (MW)", "Share of detections at or above this FRP (%)");
    frpCurve.innerHTML = curve || "";
    frpBasis.textContent = summary.basis;
    frpPanel.hidden = false;
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
    renderFrp(data);
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
