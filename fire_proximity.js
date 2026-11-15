// Annual reported-fire frequencies share the selected point with fire history.
globalThis.FireProximity = (() => {
  const status = document.getElementById("fire-proximity-status");
  const content = document.getElementById("fire-proximity-content");
  const radius = document.getElementById("fire-proximity-radius");
  const refresh = document.getElementById("fire-proximity-refresh");
  const fit = document.getElementById("fire-proximity-show");
  const exportButton = document.getElementById("fire-proximity-export");
  const rows = document.getElementById("fire-proximity-rows");
  const years = document.getElementById("fire-proximity-years");
  const chart = document.getElementById("fire-proximity-chart");
  const sizePanel = document.getElementById("fire-proximity-size");
  const sizeRows = document.getElementById("fire-proximity-size-rows");
  const curveRows = document.getElementById("fire-proximity-curve-rows");
  const curveChart = document.getElementById("fire-proximity-curve-chart");
  let map = null, selection = null, controller = null, timer = null, ring = null, data = null, curveListener = null;

  // Lets the trigger/payout controls follow the selected radius's burned-area curve.
  function notifyCurve(curve, message) {
    try { curveListener?.(curve ? {curve, baseline: data.baseline, source_name: data.source_name, source_url: data.source_url} : null, message); }
    catch (error) { console.error(error); }
  }

  function resetResult() {
    if (ring) map.removeLayer(ring);
    ring = null;
    data = null;
    content.hidden = true;
    rows.replaceChildren();
    years.replaceChildren();
    chart.replaceChildren();
    sizeRows.replaceChildren();
    curveRows.replaceChildren();
    curveChart.replaceChildren();
    sizePanel.hidden = true;
    document.getElementById("fire-proximity-year-details").open = false;
    exportButton.disabled = fit.disabled = radius.disabled = true;
  }

  function clear(message = "Select a location to calculate annual nearby-fire frequencies.") {
    controller?.abort();
    controller = null;
    clearTimeout(timer);
    selection = null;
    resetResult();
    refresh.disabled = true;
    status.textContent = message;
  }

  function selectedRadius() {
    if (!data || !selection) return;
    const selected = data.frequencies.find(item => item.radius_km === Number(radius.value));
    if (!selected) return;
    if (ring) map.removeLayer(ring);
    ring = L.circle(selection, { radius: selected.radius_km * 1000, color: "#2563eb", weight: 2, fillColor: "#60a5fa", fillOpacity: 0.08, interactive: false }).addTo(map);
    document.getElementById("fire-proximity-value").textContent = `${selected.frequency_pct.toFixed(1)}%`;
    document.getElementById("fire-proximity-calculation").textContent = `${selected.years_with_fire} of ${selected.total_years} baseline years had at least one reported wildfire within ${selected.radius_km} km of ${selection.area ? "the area center" : "your point"}. Each year counts once.`;
    for (const row of rows.children) row.dataset.selected = String(row.dataset.radius === radius.value);
    renderCurve(selected.radius_km);
    notifyCurve(data.size_exceedance?.area_exceedance?.curves?.find(item => item.radius_km === selected.radius_km) || null,
      "Burned-area trigger levels are unavailable for this radius.");
  }

  function curveSVG(points) {
    // X: burned area (log scale); Y: annual probability (%). Two series: rate-based and observed years.
    const width = 620, height = 316, left = 64, right = 24, top = 38, bottom = 58;
    const plotW = width - left - right, plotH = height - top - bottom;
    const low = Math.log10(points[0].min_acres), high = Math.log10(points.at(-1).min_acres) || low + 1;
    const x = acres => left + (Math.log10(acres) - low) / (high - low || 1) * plotW;
    const y = pct => top + (1 - pct / 100) * plotH;
    let svg = "";
    for (const pct of [0, 25, 50, 75, 100]) svg += `<line x1="${left}" y1="${y(pct)}" x2="${width - right}" y2="${y(pct)}" stroke="#e2e8f0"/><text x="${left - 8}" y="${y(pct) + 4}" text-anchor="end" font-size="11">${pct}%</text>`;
    for (const point of points) svg += `<text x="${x(point.min_acres)}" y="${height - bottom + 18}" text-anchor="middle" font-size="10">${number(point.min_acres, 1)}</text>`;
    svg += `<line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" stroke="#94a3b8"/><line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" stroke="#94a3b8"/>`;
    const series = [["annual_probability_pct", "#dc2626", "4 3", "Annual probability (rate-based)"], ["empirical_years_pct", "#1d4ed8", "", "Share of years that had one"]];
    series.forEach(([key, color, dash, label], index) => {
      svg += `<polyline points="${points.map(p => `${x(p.min_acres)},${y(p[key])}`).join(" ")}" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="${dash}"/>`;
      for (const p of points) svg += `<circle cx="${x(p.min_acres)}" cy="${y(p[key])}" r="3.5" fill="${color}"><title>${label}: ${p[key].toFixed(1)}% for ${number(p.min_acres, 1)}+ acres</title></circle>`;
      svg += `<line x1="${left + 12 + index * 250}" y1="10" x2="${left + 36 + index * 250}" y2="10" stroke="${color}" stroke-width="2" stroke-dasharray="${dash}"/><text x="${left + 42 + index * 250}" y="14" font-size="11">${label}</text>`;
    });
    svg += `<text x="${left + plotW / 2}" y="${height - 12}" text-anchor="middle" font-size="12">Reported burned area of a single fire, at least (acres, log scale)</text>` +
      `<text transform="translate(16 ${top + plotH / 2}) rotate(-90)" text-anchor="middle" font-size="12">Chance per year</text>`;
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Burned-area exceedance curve"><title>Burned-area exceedance curve</title>${svg}</svg>`;
  }

  function renderCurve(distance) {
    curveRows.replaceChildren();
    curveChart.replaceChildren();
    const curve = data?.size_exceedance?.area_exceedance?.curves?.find(item => item.radius_km === distance);
    const summary = document.getElementById("fire-proximity-curve-summary");
    if (!curve || !Array.isArray(curve.points) || !curve.points.length) { summary.textContent = "Burned-area exceedance is unavailable."; return; }
    document.getElementById("fire-proximity-curve-caption").textContent = `Reported fires of at least each size within ${distance} km, ${data.baseline.start_year}–${data.baseline.end_year}`;
    summary.textContent = curve.fire_count
      ? `Within ${distance} km: ${number(curve.fire_count)} reported fires, about ${number(curve.expected_annual_reported_acres, 1)} reported acres per year on average. Largest: ${number(curve.largest_reported_acres, 1)} acres.`
      : `No reported fires with a size within ${distance} km.`;
    for (const point of curve.points) {
      const row = document.createElement("tr");
      cells(row, [`${number(point.min_acres, 1)} acres`, number(point.fires_at_or_above), point.expected_annual_exceedances.toFixed(3),
        `${point.annual_probability_pct.toFixed(1)}%`, `${point.empirical_years_pct.toFixed(1)}%`,
        !Number.isFinite(point.return_period_years) ? "Not observed" : point.return_period_years < 1.05 ? "About every year" : `1 in ${number(point.return_period_years, 1)} years`]);
      curveRows.append(row);
    }
    if (curve.points.length > 1) curveChart.innerHTML = curveSVG(curve.points);
  }

  function cells(row, values) {
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
  }

  function render(payload) {
    if (payload.status === "unavailable") {
      status.textContent = payload.reason || "Annual nearby-fire frequency is unavailable for this location.";
      notifyCurve(null, status.textContent);
      return;
    }
    if (payload.status !== "available" || !payload.baseline || !Number.isInteger(payload.baseline.year_count) || payload.baseline.year_count < 20 ||
        !Array.isArray(payload.frequencies) || payload.frequencies.length !== 3 || !Array.isArray(payload.annual_records) ||
        payload.annual_records.length !== payload.baseline.year_count || ![5, 10, 20].every(distance => payload.frequencies.some(item =>
          item.radius_km === distance && Number.isFinite(item.frequency_pct) && item.frequency_pct >= 0 && item.frequency_pct <= 100 &&
          item.total_years === payload.baseline.year_count && Number.isInteger(item.years_with_fire) && item.years_with_fire >= 0 && item.years_with_fire <= item.total_years))) {
      throw new Error("Unexpected annual fire-frequency response. Restart the Python server and retry.");
    }
    data = payload;
    status.textContent = `Published baseline: ${data.baseline.start_year}–${data.baseline.end_year} (${data.baseline.year_count} calendar years). Source: USFS FPA FOD. ${data.county.name}, ${data.county.state}.`;
    for (const item of data.frequencies) {
      const row = document.createElement("tr");
      row.dataset.radius = String(item.radius_km);
      const interval = item.reference_interval;
      cells(row, [`Within ${item.radius_km} km`, `${item.years_with_fire} / ${item.total_years}`, `${item.frequency_pct.toFixed(1)}%`,
        Number.isFinite(interval?.minimum_pct) && Number.isFinite(interval?.maximum_pct)
          ? `${interval.minimum_pct.toFixed(1)}–${interval.maximum_pct.toFixed(1)}%` : "Unavailable"]);
      rows.append(row);
    }
    for (const item of data.annual_records) {
      const row = document.createElement("tr");
      cells(row, [String(item.year), Number.isFinite(item.nearest_distance_within_20_km)
        ? `${item.nearest_distance_within_20_km.toFixed(2)} km · ${item.nearest_fire.name} · ${item.nearest_fire.discovery_date}`
        : "No eligible record within 20 km", ...[5, 10, 20].map(distance => item.within_radii[String(distance)] ? "Yes" : "No")]);
      years.append(row);
    }
    renderSize(data.size_exceedance);
    drawHazardChart(chart, data.frequencies.map(item => ({severity: item.radius_km, frequency_pct: item.frequency_pct})), "Search radius (km)", "Historical annual frequency (%)", 20);
    document.getElementById("fire-proximity-coverage").textContent = data.baseline.coverage_assumption;
    document.getElementById("fire-proximity-checked").textContent = `Archive checked: ${data.checked_at}. New archive editions can extend this baseline; ongoing WFIGS reports appear only in the nearest-fire result below.`;
    content.hidden = false;
    exportButton.disabled = fit.disabled = radius.disabled = false;
    selectedRadius();
  }

  const number = (value, digits = 0) => value.toLocaleString("en-US", {maximumFractionDigits: digits});

  function renderSize(size) {
    if (!size || !Array.isArray(size.threshold_frequencies) || !size.footprint_reach || !Array.isArray(size.burned_area)) return;
    for (const acres of size.size_thresholds_acres) {
      const row = document.createElement("tr");
      cells(row, [`${number(acres)}+ acres`, ...[5, 10, 20].map(distance => {
        const item = size.threshold_frequencies.find(entry => entry.min_acres === acres && entry.radius_km === distance);
        return item && Number.isFinite(item.frequency_pct) ? `${item.years_with_fire} / ${item.total_years} (${item.frequency_pct.toFixed(1)}%)` : "Unavailable";
      })]);
      sizeRows.append(row);
    }
    const reach = size.footprint_reach;
    document.getElementById("fire-proximity-reach").textContent = reach.years_with_reach > 0
      ? `In ${reach.years_with_reach} of ${reach.total_years} years, a reported fire's equivalent-circle footprint covered this point (${reach.fires.map(fire => `${fire.name}, ${fire.discovery_date}, ${number(fire.reported_acres)} acres`).join("; ")}).`
      : Number.isFinite(reach.closest_footprint_gap_km)
        ? `No reported fire's equivalent-circle footprint covered this point. The closest footprint edge was ${reach.closest_footprint_gap_km.toFixed(2)} km away.`
        : "No sized fire records were reported within 20 km.";
    document.getElementById("fire-proximity-burned").textContent = size.burned_area.map(item =>
      `Within ${item.radius_km} km: ${number(item.overlapping_reported_acres)} reported acres over ${item.total_years} years, about ${item.mean_annual_burned_share_pct.toFixed(3)}% of the circle per year` +
      (Number.isFinite(item.equivalent_return_interval_years) ? ` (equivalent to once every ${number(item.equivalent_return_interval_years)} years).` : ".")).join(" ");
    sizePanel.hidden = false;
  }

  async function request() {
    if (!selection) return;
    controller?.abort();
    clearTimeout(timer);
    const current = new AbortController(), point = selection;
    controller = current;
    resetResult();
    refresh.disabled = true;
    status.textContent = "Calculating annual frequencies from the published fire archive…";
    const timeout = setTimeout(() => current.abort(), 55000);
    try {
      const response = await fetch(`/api/v1/fire-proximity?${new URLSearchParams({lat: point.lat, lon: point.lng})}`, {signal: current.signal});
      const payload = await response.json();
      if (controller !== current || selection !== point || current.signal.aborted) return;
      if (!response.ok) throw new Error(payload.error?.message || "Annual fire-frequency lookup failed.");
      render(payload.data);
    } catch (error) {
      if (controller !== current || selection !== point) return;
      resetResult();
      status.textContent = current.signal.aborted ? "Annual fire-frequency lookup timed out. Refresh to retry." : `Annual fire frequency unavailable: ${error.message}`;
      notifyCurve(null, status.textContent);
    } finally {
      clearTimeout(timeout);
      if (controller === current && selection === point) {
        controller = null;
        refresh.disabled = false;
        timer = setTimeout(() => { if (!document.hidden) request(); }, 300000);
      }
    }
  }

  radius.addEventListener("change", selectedRadius);
  refresh.addEventListener("click", request);
  fit.addEventListener("click", () => { if (ring) map.fitBounds(ring.getBounds(), {padding: [35, 35]}); });
  exportButton.addEventListener("click", () => {
    if (!data || !selection) return;
    const lines = [["row_type", "selected_latitude", "selected_longitude", "baseline_start", "baseline_end", "radius_km", "year", "qualifies", "years_with_fire", "total_years", "frequency_pct", "interval_low_pct", "interval_high_pct", "nearest_distance_within_20_km", "nearest_fire_id", "nearest_fire_name", "discovery_date", "record_url", "source_url", "checked_at", "frequency_basis", "source_layer", "coverage_assumption", "interval_method"]];
    const prefix = [selection.lat, selection.lng, data.baseline.start_year, data.baseline.end_year];
    for (const item of data.frequencies) lines.push(["summary", ...prefix, item.radius_km, "", "", item.years_with_fire, item.total_years, item.frequency_pct, item.reference_interval.minimum_pct, item.reference_interval.maximum_pct, "", "", "", "", "", data.source_url, data.checked_at]);
    for (const item of data.annual_records) for (const distance of [5, 10, 20]) lines.push(["year", ...prefix, distance, item.year, item.within_radii[String(distance)], "", data.baseline.year_count, "", "", "", item.nearest_distance_within_20_km ?? "", item.nearest_fire?.id || "", item.nearest_fire?.name || "", item.nearest_fire?.discovery_date || "", item.nearest_fire?.record_url || "", data.source_url, data.checked_at]);
    for (const line of lines.slice(1)) line.push(data.frequency_basis, data.baseline.source_layer, data.baseline.coverage_assumption, "wilson_95_binomial_reference");
    function csv(value) {
      let text = String(value);
      if (typeof value === "string" && /^[=+@\-\t\r]/.test(text)) text = "'" + text;
      return `"${text.replaceAll('"', '""')}"`;
    }
    const url = URL.createObjectURL(new Blob([lines.map(line => line.map(csv).join(",")).join("\r\n") + "\r\n"], {type: "text/csv;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = `fire_proximity_${selection.lat.toFixed(4)}_${selection.lng.toFixed(4)}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && selection && !controller) request(); });
  return {clear, onCurve(listener) { curveListener = listener; }, load(leafletMap, point, area = false) {
    clear();
    map = leafletMap;
    selection = {lat: point.lat, lng: point.lng, area};
    request();
  }};
})();
