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
  let map = null, selection = null, controller = null, timer = null, ring = null, data = null;

  function resetResult() {
    if (ring) map.removeLayer(ring);
    ring = null;
    data = null;
    content.hidden = true;
    rows.replaceChildren();
    years.replaceChildren();
    chart.replaceChildren();
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
    drawHazardChart(chart, data.frequencies.map(item => ({severity: item.radius_km, frequency_pct: item.frequency_pct})), "Search radius (km)", "Historical annual frequency (%)", 20);
    document.getElementById("fire-proximity-coverage").textContent = data.baseline.coverage_assumption;
    document.getElementById("fire-proximity-checked").textContent = `Archive checked: ${data.checked_at}. New archive editions can extend this baseline; ongoing WFIGS reports appear only in the nearest-fire result below.`;
    content.hidden = false;
    exportButton.disabled = fit.disabled = radius.disabled = false;
    selectedRadius();
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
  return {clear, load(leafletMap, point, area = false) {
    clear();
    map = leafletMap;
    selection = {lat: point.lat, lng: point.lng, area};
    request();
  }};
})();
