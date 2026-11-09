// All-history reported wildfire lookup, independent of the selected hazard curve.
globalThis.FireHistory = (() => {
  const status = document.getElementById("nearest-fire-status");
  const details = document.getElementById("nearest-fire-details");
  const refresh = document.getElementById("nearest-fire-refresh");
  const show = document.getElementById("nearest-fire-show");
  let selection = null;
  let map = null;
  let controller = null;
  let timer = null;
  let layers = [];
  let firePoint = null;

  function removeResult() {
    for (const layer of layers) map.removeLayer(layer);
    layers = [];
    firePoint = null;
    details.replaceChildren();
    show.hidden = true;
  }

  function clear(message = "Select a location to find the nearest recorded wildfire.") {
    controller?.abort();
    controller = null;
    clearTimeout(timer);
    selection = null;
    removeResult();
    refresh.disabled = true;
    status.textContent = message;
  }

  function paragraph(text) {
    const p = document.createElement("p");
    p.textContent = text;
    details.append(p);
  }

  async function request() {
    if (!selection) return;
    controller?.abort();
    clearTimeout(timer);
    const current = new AbortController();
    controller = current;
    const point = selection;
    removeResult();
    refresh.disabled = true;
    status.textContent = "Searching all available fire history…";
    const timeout = setTimeout(() => current.abort(), 55000);
    try {
      const response = await fetch(`/api/v1/nearest-fire?${new URLSearchParams({ lat: point.lat, lon: point.lng })}`, { signal: current.signal });
      const payload = await response.json();
      if (current.signal.aborted || controller !== current || selection !== point) return;
      if (!response.ok) throw new Error(payload.error?.message || "Fire-history lookup failed.");
      const data = payload.data;
      if (!data || !Array.isArray(data.sources) || data.time_window !== "all_available_history") {
        throw new Error("Unexpected fire-history response. Restart the Python server and retry.");
      }
      if (data.status === "not_found" && data.fire === null) {
        status.textContent = "No eligible dated wildfire record found in the connected datasets.";
      } else {
        const fire = data.fire;
        if (data.status !== "available" || !fire ||
            !Number.isFinite(fire.latitude) || Math.abs(fire.latitude) > 90 ||
            !Number.isFinite(fire.longitude) || Math.abs(fire.longitude) > 180 ||
            !Number.isFinite(fire.distance_km) || fire.distance_km < 0) {
          throw new Error("Unexpected fire-history result.");
        }
        firePoint = { lat: fire.latitude, lng: fire.longitude };
        status.textContent = `${fire.name} · ${fire.distance_km.toFixed(2)} km from ${point.area ? "the area center" : "your selected point"} · reported discovery: ${fire.discovery_date}`;
        paragraph(`Fire location: ${fire.latitude.toFixed(6)}, ${fire.longitude.toFixed(6)}. ${Number.isFinite(fire.reported_acres) ? `Reported size: ${fire.reported_acres.toLocaleString()} ${fire.reported_acres === 1 ? "acre" : "acres"}.` : "Reported size unavailable."}`);
        paragraph(`Source: ${fire.source_name}. ${fire.date_note}${fire.record_updated_at ? ` Incident record updated: ${fire.record_updated_at}.` : ""}`);
        const recordUrl = new URL(fire.record_url);
        if (recordUrl.protocol === "https:" && ["apps.fs.usda.gov", "services3.arcgis.com"].includes(recordUrl.hostname)) {
          const link = document.createElement("a");
          link.href = recordUrl.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "View source fire record";
          details.append(link);
        }
        const popup = document.createElement("span");
        popup.textContent = `${fire.name} · ${fire.discovery_date} · ${fire.distance_km.toFixed(2)} km from selection`;
        layers.push(L.circleMarker(firePoint, { radius: 11, color: "#991b1b", weight: 3, fillColor: "#ef4444", fillOpacity: 0.95 }).addTo(map).bindPopup(popup));
        layers.push(L.polyline([point, firePoint], { color: "#b91c1c", weight: 2, dashArray: "6 6" }).addTo(map));
        show.hidden = false;
      }
      paragraph(`All available history; no date filter. ${data.sources.map(source => source.coverage).join(" ")}`);
      paragraph(`Last checked: ${data.checked_at}. Checks automatically every 5 minutes while this page is visible.`);
      paragraph(data.message);
    } catch (error) {
      if (controller !== current || selection !== point) return;
      removeResult();
      status.textContent = current.signal.aborted
        ? "Fire-history search timed out. Refresh to retry."
        : `Fire history unavailable: ${error.message} Refresh to retry.`;
    } finally {
      clearTimeout(timeout);
      if (controller === current && selection === point) {
        controller = null;
        refresh.disabled = false;
        timer = setTimeout(() => {
          if (!document.hidden) request();
        }, 300000);
      }
    }
  }

  refresh.addEventListener("click", request);
  show.addEventListener("click", () => {
    if (selection && firePoint) map.fitBounds(L.latLngBounds([selection, firePoint]), { padding: [45, 45], maxZoom: 15 });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && selection && !controller) request();
  });
  return {
    clear,
    load(leafletMap, point, area = false) {
      clear();
      map = leafletMap;
      selection = { lat: point.lat, lng: point.lng, area };
      request();
    },
  };
})();
