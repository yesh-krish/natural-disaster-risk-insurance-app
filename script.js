// FEMA's public NFHL MapServer layer 28 is "Flood Hazard Zones" (S_Fld_Haz_Ar).
const FEMA_QUERY_URL =
  "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query";

const fields = [
  ["FLD_ZONE", "Flood zone"],
  ["ZONE_SUBTY", "Zone subtype"],
  ["SFHA_TF", "Special Flood Hazard Area (T/F)"],
  ["STATIC_BFE", "Static base flood elevation"],
  ["DEPTH", "Depth"],
];

const map = L.map("map").setView([29.4241, -98.4936], 11);
// OpenFreeMap serves OpenStreetMap-derived vector tiles without an API key.
// Its Leaflet adapter keeps Leaflet's markers and click events working as before.
L.maplibreGL({
  style: "https://tiles.openfreemap.org/styles/liberty",
  attribution: '&copy; <a href="https://openfreemap.org/">OpenFreeMap</a> · ' +
    '<a href="https://www.openmaptiles.org/">OpenMapTiles</a> · ' +
    '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const coordinates = document.getElementById("coordinates");
const status = document.getElementById("status");
const results = document.getElementById("results");
let marker;
let stationMarkers = [];
let stationPoints = [];
let pendingStationRequest;
const stationStatus = document.getElementById("station-status");
const stationDetails = document.getElementById("station-details");
const stationList = document.getElementById("station-list");
const stationCount = document.getElementById("station-count");
const stationShow = document.getElementById("station-show");
const stationRetry = document.getElementById("station-retry");
const heatStatus = document.getElementById("heat-status");
const heatList = document.getElementById("heat-list");
document.getElementById("heat-refresh").addEventListener("click", refreshStations);

function refreshStations() {
  if (!selectedPoint) return;
  clearStation();
  loadNearestStations(selectedPoint.lat, selectedPoint.lng, locationVersion);
}
stationRetry.addEventListener("click", refreshStations);
stationCount.addEventListener("input", refreshStations);

function validStationCount() {
  return /^[0-9]+$/.test(stationCount.value) && Number(stationCount.value) >= 1 && Number(stationCount.value) <= 50;
}

function clearStation() {
  pendingStationRequest?.abort();
  pendingStationRequest = null;
  stationMarkers.forEach(layer => map.removeLayer(layer));
  stationMarkers = [];
  stationPoints = [];
  stationList.replaceChildren();
  heatList.replaceChildren();
  heatStatus.textContent = "Heat screening cleared. Select a location or refresh station reports.";
  stationDetails.textContent = "";
  stationShow.hidden = true;
  stationRetry.hidden = true;
}

stationShow.addEventListener("click", () => {
  if (stationPoints.length && selectedPoint) {
    map.fitBounds(L.latLngBounds([selectedPoint, ...stationPoints]), { padding: [45, 45], maxZoom: 14 });
  }
});

async function loadNearestStations(lat, lng, version) {
  if (!validStationCount()) {
    stationStatus.textContent = "n must be a positive integer from 1 to 50.";
    heatStatus.textContent = "Heat screening unavailable: station count must be an integer from 1 to 50.";
    return;
  }
  const controller = new AbortController();
  pendingStationRequest = controller;
  const timeout = setTimeout(() => {
    if (version !== locationVersion || pendingStationRequest !== controller) return;
    controller.abort();
    stationStatus.textContent = "The station service is taking too long. Please retry the station search.";
    heatStatus.textContent = "Heat screening unavailable: station request timed out. Refresh to retry.";
    stationRetry.hidden = false;
  }, 15000);
  stationRetry.hidden = true;
  stationStatus.textContent = `Finding the nearest ${stationCount.value} METAR monitoring stations…`;
  heatStatus.textContent = "Loading station observation snapshot for heat screening…";
  try {
    const params = new URLSearchParams({ network: "awc_metar", n: stationCount.value, lat, lon: lng });
    const response = await fetch(`/api/v1/monitoring-stations?${params}`, { signal: controller.signal });
    if (controller.signal.aborted || version !== locationVersion || pendingStationRequest !== controller) return;
    if (response.status === 404) throw new Error("Restart the Python server to enable monitoring-station lookup.");
    const payload = await response.json();
    if (controller.signal.aborted || version !== locationVersion || pendingStationRequest !== controller) return;
    if (!response.ok) throw new Error(payload.error?.message || "Monitoring-station lookup failed.");
    const data = payload.data;
    if (!data || !Array.isArray(data.stations)) throw new Error("Unexpected station response. Restart the Python server and retry.");
    stationDetails.textContent = data.message || "";
    stationStatus.textContent = `${data.returned_count} of ${data.requested_count} requested stations found within ${data.search_radius_km} km of ${selectedArea ? "area center" : "selected point"}, nearest first.${data.fewer_than_requested ? " Fewer eligible stations are available in this search area." : ""}`;
    stationDetails.textContent += ` Catalog fetched: ${data.catalog_fetched_at}. ${data.coverage}`;
    renderHeatStations(data.stations);
    for (const [index, station] of data.stations.entries()) {
      const point = { lat: station.latitude, lng: station.longitude };
      const label = `${index + 1}. ${station.name} (${station.id}) · ${station.distance_km.toFixed(2)} km`;
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `${station.name} (${station.id}) · ${station.distance_km.toFixed(2)} km`;
      const details = document.createElement("p");
      details.textContent = `${station.network}. Variables: ${station.measured_variables.join(", ")}. ${station.variables_note} Coordinates: ${station.latitude.toFixed(5)}, ${station.longitude.toFixed(5)}.`;
      const reading = document.createElement("p");
      const observation = station.observation;
      reading.textContent = observation?.observed_at
        ? `${observation.status === "stale" ? "STALE — " : observation.status === "missing" ? "Missing measurements — " : "Latest report — "}${observation.observed_at} (UTC): ${(observation.values || []).map(value => `${value.variable}: ${value.value} ${value.unit}`).join("; ")}. ${observation.message}`
        : observation?.message || "Latest observations unavailable.";
      item.append(title, details, reading);
      // Provider text is always textContent; URLs are restricted to the data site.
      if (/^https:\/\/aviationweather\.gov\/data\/metar\/\?id=[A-Z0-9]+$/.test(station.url)) {
        const link = document.createElement("a");
        link.href = station.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = `View ${station.id} station data`;
        item.append(link);
      }
      stationList.append(item);
      const popup = document.createElement("span");
      popup.textContent = label;
      const layer = L.circleMarker(point, { radius: index ? 9 : 12, color: "#9a3412", weight: 3, fillColor: "#fb923c", fillOpacity: 0.9 }).addTo(map).bindPopup(popup);
      stationMarkers.push(layer);
      stationPoints.push(point);
    }
    stationShow.hidden = !stationPoints.length;
  } catch (error) {
    if (!controller.signal.aborted && version === locationVersion && pendingStationRequest === controller) {
      stationStatus.textContent = error.message || "Monitoring-station service is unavailable.";
      heatStatus.textContent = "Heat screening unavailable: station service failed. Refresh to retry.";
      stationRetry.hidden = false;
    }
  } finally {
    clearTimeout(timeout);
    if (pendingStationRequest === controller) pendingStationRequest = null;
  }
}

function renderHeatStations(stations) {
  heatList.replaceChildren();
  heatStatus.textContent = stations.length
    ? `Observation snapshot for ${stations.length} nearby stations, nearest first. Heat Index is withheld for reports older than 2 hours, missing inputs or unsupported conditions. No area average is calculated.`
    : "No eligible METAR stations within 200 km. Heat screening unavailable; this does not imply low heat hazard.";
  for (const station of stations) {
    const item = document.createElement("li");
    const heat = station.observation?.heat;
    const title = document.createElement("strong");
    title.textContent = `${station.name} (${station.id}) · ${station.distance_km.toFixed(2)} km`;
    const reading = document.createElement("p");
    const temperature = Number.isFinite(heat?.air_temperature_c)
      ? `Air temperature: ${heat.air_temperature_c.toFixed(1)} °C / ${(heat.air_temperature_c * 9 / 5 + 32).toFixed(1)} °F. `
      : "Air temperature unavailable. ";
    const timestamp = station.observation?.observed_at;
    const age = station.observation?.age_minutes;
    const time = timestamp ? `Report: ${timestamp} (UTC)${Number.isFinite(age) ? `; ${age} minutes old at lookup` : ""}. ` : "Report time unavailable. ";
    const available = heat?.status === "available" && Number.isFinite(heat.heat_index_c) && Number.isFinite(heat.heat_index_f);
    reading.textContent = temperature + time + (available
      ? `Derived RH: ${heat.relative_humidity_pct.toFixed(1)}%. Heat Index (shade): ${heat.heat_index_c.toFixed(1)} °C / ${heat.heat_index_f.toFixed(1)} °F. NWS band: ${heat.category}. `
      : "Heat Index unavailable. ") + (heat?.reason || "Heat screening data missing; restart the Python server if it predates this feature.");
    // Keep provider text inert, just as in the station list.
    item.append(title, reading);
    heatList.append(item);
  }
}
let pendingRequest;
let pendingDepthRequest;
let pendingGeocodeRequest;
let pendingHazardRequest;
let locationVersion = 0;
let selectedPoint = null;
let selectedArea = null;
let areaLayer = null;
let areaPreview = null;
let areaStart = null;
let drawingArea = false;
let areaSampleLayers = [];
const selectionMode = document.getElementById("selection-mode");
const selectionHelp = document.getElementById("selection-help");
const areaSummary = document.getElementById("area-summary");
const redrawArea = document.getElementById("redraw-area");
const cancelArea = document.getElementById("cancel-area");
let femaAttributes = null;
let resolvedDisaster = null;
const disasterSelect = document.getElementById("disaster-select");
const siteClassSelect = document.getElementById("site-class");
const hazardPanel = document.getElementById("hazard-panel");
const hazardStatus = document.getElementById("hazard-status");
const hazardChart = document.getElementById("hazard-chart");
const hazardRows = document.getElementById("hazard-point-rows");
const hazardSource = document.getElementById("hazard-source");
const hazardNotes = document.getElementById("hazard-notes");
const relevancePanel = document.getElementById("relevance-panel");
const relevanceStatus = document.getElementById("relevance-status");
const relevanceReason = document.getElementById("relevance-reason");
const relevanceSource = document.getElementById("relevance-source");
const relevanceChecks = document.getElementById("relevance-checks");
const disasterTypes = {
  auto: { name: "Choose most relevant", metric: "Location-based selection", unit: "", source: "FEMA county context + local government curves", note: "Select a location to choose a disaster using regional hazard data and available local curves." },
  flood: { name: "Flood", metric: "Peak inundation depth", unit: "m", source: "TWDB + FEMA", note: "Flood depths cover installed Texas tiles. FEMA polygons provide flood-zone context; missing data does not establish no flood risk." },
  earthquake: { name: "Earthquake", metric: "Peak ground acceleration", unit: "%g", source: "USGS NSHM", note: "Site class affects modeled shaking. BC is the initial assumption; select the appropriate site class." },
  wildfire: { name: "Fire / Wildfire", metric: "Flame length", unit: "m", source: "USDA Forest Service", note: "The graph uses burn probability and conditional flame-length probabilities. Wildfire Hazard Potential is shown as context." },
  fireline_intensity: { name: "Wildfire / Fireline intensity", metric: "Fireline intensity", unit: "kW/m", source: "Texas A&M Forest Service / TxWRAP", note: "Native weather-weighted heading fireline intensity for Texas. This is modeled fire behavior, not a live reading or annual exceedance probability. Heat flux at buildings and ember exposure are separate." },
  potential_fire_intensity: { name: "Wildfire / Potential intensity", metric: "Potential surface-fire intensity", unit: "kW/m", source: "ERA5 + LANDFIRE + Rothermel–Byram", note: "Historical weather is replayed on a fixed landscape to estimate potential surface-fire intensity. The annual curve measures potential conditions, not actual wildfire occurrence or damage. Research prototype; Point mode covers supported LANDFIRE cells in CONUS, Alaska and Hawaii." },
  cyclone: { name: "Wind", metric: "Maximum sustained wind (tropical cyclones)", unit: "kn", source: "NOAA NHC", note: "Tropical-cyclone wind only: NOAA's best-track wind footprints support sustained-wind historical frequencies. This is not an all-wind model; thunderstorms, tornadoes, other windstorms and local peak gusts are not modeled." },
  rainfall: { name: "Rainfall", metric: "Maximum 24-hour rainfall", unit: "mm", source: "NOAA Atlas 14", note: "Rainfall severity is accumulation depth over 24 hours, separate from cyclone wind speed." },
  heat: { name: "Heat", metric: "Annual maximum daily air temperature", unit: "°C", source: "NOAA GHCN-Daily", note: "Historical annual heat exceedance at a qualifying nearby US station, 1991–2020. Station distance and complete-year coverage are reported. This measures daily air-temperature extremes; heat-wave duration and construction WBGT require separate inputs." },
};

const triggerSelect = document.getElementById("trigger-select");
const triggerHelp = document.getElementById("trigger-help");
const triggerResult = document.getElementById("trigger-result");
const triggerProbability = document.getElementById("trigger-probability");
const triggerExplanation = document.getElementById("trigger-explanation");
const triggerPayout = document.getElementById("trigger-payout");
const poolContribution = document.getElementById("pool-contribution");
const poolCalculation = document.getElementById("pool-calculation");
const poolCurrency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
let triggerData = null;
let triggerLevels = [];
let intensityData = null;
let historicalHeatData = null;
let potentialFireData = null;

function renderTriggerComparison(payout) {
  const rows = document.getElementById("comparison-rows");
  const content = document.getElementById("comparison-content");
  const status = document.getElementById("comparison-status");
  rows.replaceChildren();
  content.hidden = !triggerData || !triggerLevels.length;
  if (content.hidden) {
    status.textContent = triggerHelp.textContent;
    return;
  }
  const data = triggerData, type = disasterTypes[data.peril];
  const basis = hazardTriggerBasis(data.peril);
  const unit = data.unit || type.unit;
  const hasPayout = payout !== null;
  const hasSelection = triggerSelect.value !== "";
  status.textContent = `${triggerLevels.length} supported threshold${triggerLevels.length === 1 ? "" : "s"} for the same ${data.area ? "area" : "location"}. ` +
    (triggerLevels.length === 1 ? "Only one threshold is available here. " : "Compare each option, then select your active trigger. ") +
    (hasPayout ? `Every row uses the same ${poolCurrency.format(payout)} payout.` : "Enter a valid payout above to compare annual contributions.");
  document.getElementById("comparison-context").textContent = `${type.name} · ${data.area ? "Area centered at" : "Location"} ${selectedPoint.lat.toFixed(5)}, ${selectedPoint.lng.toFixed(5)}` +
    `${data.peril === "earthquake" ? ` · Site class ${siteClassSelect.value}` : ""} · Source: ${data.source_name || type.source}`;
  document.getElementById("comparison-severity").textContent = `${data.metric || type.metric} threshold (${unit})`;
  document.getElementById("comparison-frequency").textContent = `${data.area ? "Average sampled " : ""}${basis.label.toLowerCase()} (%)`;
  document.getElementById("comparison-coverage").hidden = !data.area;
  document.getElementById("comparison-note").textContent =
    (data.area ? `${data.area.method} Each row averages available grid-point probabilities at that severity; coverage may differ between rows. This is not the probability of an event anywhere in the area. ` : "Only supplied source levels are compared; missing levels are omitted. ") +
    (data.area && !["cyclone", "wildfire"].includes(data.peril) ? "Modeled one-year exceedance probabilities. " : basis.note + " ") +
    "Contribution = (annual exceedance percentage ÷ 100) × fixed payout, assuming one payout in a year; excludes fees and other pricing adjustments. Ranges preserve differing source frequencies at the same rounded threshold. Each row is an alternative; contributions are not added together.";
  triggerLevels.forEach((level, index) => {
    const row = document.createElement("tr");
    const selected = hasSelection && Number(triggerSelect.value) === index;
    row.dataset.selected = String(selected);
    const threshold = document.createElement("th");
    threshold.scope = "row";
    threshold.textContent = `≥ ${formatHazardNumber(level.severity)} ${unit}`;
    if (data.peril === "wildfire") threshold.textContent += ` (${formatHazardNumber(level.severity / 0.3048)} ft)`;
    row.append(threshold);
    const range = level.minimum !== level.maximum;
    const contribution = hasPayout ? hazardPoolContribution(level, payout) : null;
    const values = [
      range ? `${formatHazardNumber(level.minimum)}–${formatHazardNumber(level.maximum)}%` : `${formatHazardNumber(level.minimum)}%`,
      hasPayout ? poolCurrency.format(payout) : "—",
      contribution ? (contribution.minimum !== contribution.maximum ? `${poolCurrency.format(contribution.minimum)}–${poolCurrency.format(contribution.maximum)}` : poolCurrency.format(contribution.minimum)) : "—",
    ];
    if (data.area) {
      const points = data.points.filter(point => point.severity === level.severity && Number.isFinite(point.frequency_pct));
      const coverage = [...new Set(points.map(point => `${point.evaluated_samples}/${data.area.sample_count} used; ${point.missing_samples} unavailable; ${point.interpolated_samples} interpolated`))];
      values.push(coverage.join(" / "));
    }
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.id = `compare-trigger-${index}`;
    button.type = "button";
    button.className = "scenario-button";
    button.textContent = selected ? "Selected" : "Use trigger";
    button.setAttribute("aria-pressed", String(selected));
    button.setAttribute("aria-label", `${selected ? "Selected trigger" : "Use trigger"}: at or above ${formatHazardNumber(level.severity)} ${unit}`);
    button.addEventListener("click", () => {
      triggerSelect.value = String(index);
      renderTriggerResult();
      document.getElementById(`compare-trigger-${index}`).focus({ preventScroll: true });
    });
    action.append(button);
    row.append(action);
    rows.append(row);
  });
}

function renderPoolContribution() {
  poolContribution.textContent = "—";
  const empty = triggerPayout.value.trim() === "";
  const payout = triggerPayout.valueAsNumber;
  const valid = !empty && triggerPayout.validity.valid && Number.isFinite(payout) && payout >= 0;
  triggerPayout.setAttribute("aria-invalid", String(!valid && (!empty || triggerPayout.validity.badInput)));
  renderTriggerComparison(valid ? payout : null);
  if (!triggerData || triggerSelect.value === "") {
    poolCalculation.textContent = "Select a location and supported trigger level to calculate the contribution.";
    return;
  }
  if (!valid) {
    poolCalculation.textContent = empty && !triggerPayout.validity.badInput
      ? "Select or enter a payout amount in USD."
      : "Enter a non-negative payout in USD with no more than two decimal places.";
    return;
  }
  const level = triggerLevels[Number(triggerSelect.value)];
  const contribution = hazardPoolContribution(level, payout);
  if (!contribution) {
    poolCalculation.textContent = "A valid trigger probability is required to calculate the contribution.";
    return;
  }
  const range = level.minimum !== level.maximum;
  poolContribution.textContent = contribution.minimum !== contribution.maximum
    ? `${poolCurrency.format(contribution.minimum)}–${poolCurrency.format(contribution.maximum)}`
    : poolCurrency.format(contribution.minimum);
  const probability = range
    ? `${formatHazardNumber(level.minimum)}–${formatHazardNumber(level.maximum)}%`
    : `${formatHazardNumber(level.minimum)}%`;
  poolCalculation.textContent = `${probability} × ${poolCurrency.format(payout)} = ${poolContribution.textContent} per year.${triggerData.area ? " Based on the average of available grid-point probabilities." : ""}`;
  if (["cyclone", "heat"].includes(triggerData.peril)) poolCalculation.textContent += " Uses observed annual frequency.";
  if (triggerData.peril === "heat") poolCalculation.textContent += " Historical 1991–2020 station-proxy illustration; not a forward-looking price.";
  if (triggerData.peril === "potential_fire_intensity") poolCalculation.textContent += " Uses historical potential-condition frequency. This trigger can be met without an actual fire; it is not wildfire loss pricing.";
}

function resetTrigger(message, state = "idle") {
  clearAreaSamples();
  triggerData = null;
  triggerLevels = [];
  triggerSelect.replaceChildren(new Option(state === "loading" ? "Loading trigger levels…" : "No trigger levels loaded", ""));
  triggerSelect.disabled = true;
  triggerHelp.textContent = message;
  triggerResult.dataset.state = state;
  triggerProbability.textContent = "—";
  triggerExplanation.textContent = message;
  renderPoolContribution();
  const basis = hazardTriggerBasis(resolvedDisaster);
  document.getElementById("trigger-result-label").textContent = basis.label;
  for (const id of ["trigger-location", "trigger-source", "trigger-method"]) {
    document.getElementById(id).hidden = true;
    document.getElementById(id).replaceChildren();
  }
}

function loadTriggerLevels(data) {
  if (data.annual_probability_available === false) {
    resetTrigger(data.trigger_unavailable_reason || "Annual probabilities are not available for this intensity dataset.", "unavailable");
    return;
  }
  const levels = hazardTriggerLevels(data);
  if (!levels.length) {
    resetTrigger(data.reason || "No supported trigger levels are available here. Try another location or hazard. Missing data does not mean zero probability.", "unavailable");
    return;
  }
  triggerData = data;
  triggerLevels = levels;
  const type = disasterTypes[data.peril];
  triggerSelect.replaceChildren(new Option("Select a trigger level", ""));
  levels.forEach((level, index) => {
    const unit = data.unit || type.unit;
    let label = `${formatHazardNumber(level.severity)} ${unit}`;
    if (data.peril === "wildfire") label += ` (${formatHazardNumber(level.severity / 0.3048)} ft flame length)`;
    triggerSelect.append(new Option(label, String(index)));
  });
  triggerSelect.disabled = false;
  const missing = data.points.filter(point => !Number.isFinite(point.severity)).length;
  triggerHelp.textContent = `${type.metric} · ${levels.length} supported level${levels.length === 1 ? "" : "s"}. ${missing ? `${missing} missing source level${missing === 1 ? " is" : "s are"} omitted. ` : ""}${data.area ? "Average probabilities at the same severity across 25 grid samples; valid brackets use log-probability interpolation." : data.peril === "heat" ? "Only observed annual-maximum temperature levels can be selected; no extrapolated tails." : "Only supplied model levels can be selected."}`;
  triggerResult.dataset.state = "ready";
  document.getElementById("trigger-result-label").textContent = hazardTriggerBasis(data.peril).label;
  triggerExplanation.textContent = "Choose a trigger level to see its exceedance result.";
  renderPoolContribution();
}

function renderTriggerResult() {
  clearAreaSamples();
  renderPoolContribution();
  if (!triggerData) return;
  const level = triggerSelect.value === "" ? null : triggerLevels[Number(triggerSelect.value)];
  triggerProbability.textContent = "—";
  for (const id of ["trigger-location", "trigger-source", "trigger-method"]) document.getElementById(id).hidden = !level;
  document.getElementById("trigger-result-label").textContent = hazardTriggerBasis(triggerData.peril).label;
  if (!level) {
    triggerResult.dataset.state = "ready";
    triggerExplanation.textContent = "Choose a trigger level to see its exceedance result.";
    return;
  }
  const data = triggerData, type = disasterTypes[data.peril];
  const basis = hazardTriggerBasis(data.peril);
  const range = level.minimum !== level.maximum;
  triggerResult.dataset.state = "available";
  triggerProbability.textContent = range
    ? `${formatHazardNumber(level.minimum)}–${formatHazardNumber(level.maximum)}%`
    : `${formatHazardNumber(level.minimum)}%`;
  triggerExplanation.textContent = `${type.name}: ${data.metric || type.metric} at or above ${formatHazardNumber(level.severity)} ${data.unit || type.unit}.`;
  const location = document.getElementById("trigger-location");
  location.textContent = `${selectedArea ? "Selected area centroid" : "Location"}: ${selectedPoint.lat.toFixed(5)}, ${selectedPoint.lng.toFixed(5)}${data.peril === "earthquake" ? ` · Site class ${siteClassSelect.value}` : ""}`;
  const source = document.getElementById("trigger-source");
  source.replaceChildren("Source: ");
  if (data.source_url && /^https:\/\//.test(data.source_url)) {
    const link = document.createElement("a");
    link.href = data.source_url;
    link.textContent = data.source_name || type.source;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    source.append(link);
  } else source.append(data.source_name || type.source);
  document.getElementById("trigger-method").textContent = basis.note + (range ? " Multiple source frequencies share this rounded level, so their range is shown; a unique probability is unavailable." : "");
  if (data.peril === "heat" && data.station) {
    location.textContent += ` · Historical station: ${data.station.name} (${data.station.id}), ${data.station.distance_km.toFixed(2)} km away. ${data.baseline?.complete_years} included years, 1991–2020.`;
  }
  if (data.area) {
    const point = data.points.find(p => p.severity === level.severity);
    const report = document.getElementById("area-average");
    report.hidden = false;
    document.getElementById("export-area").disabled = false;
    const calculation = `${formatHazardNumber(point.sum_frequency_pct)}% ÷ ${point.evaluated_samples} valid samples = ${formatHazardNumber(point.frequency_pct)}%.`;
    report.textContent = `${calculation} Used ${point.evaluated_samples}/${data.area.sample_count} samples; ${point.missing_samples} unavailable at this threshold; ${point.interpolated_samples} interpolated. ${point.missing_samples ? "Partial coverage: this average describes the available samples only." : "All grid samples contribute to this average."}`;
    document.getElementById("trigger-result-label").textContent = `Average sampled ${basis.label.toLowerCase()}`;
    document.getElementById("trigger-method").textContent = `${data.area.method} ${["cyclone", "wildfire"].includes(data.peril) ? basis.note : "Modeled local chance of meeting or exceeding this severity in one year."} This is a mean of local probabilities, not the probability of an event anywhere in the area. It estimates the grid-point average rather than every raster cell.`;
    document.getElementById("area-sample-details").hidden = false;
    document.getElementById("area-sample-value").textContent = "Exceedance (%)";
    document.getElementById("area-sample-context").textContent = `${type.name}: ${formatHazardNumber(level.severity)} ${data.unit || type.unit}. Blue dots have a value; gray dots are unavailable at this threshold.`;
    document.getElementById("area-sample-mean").textContent = `Arithmetic mean: ${calculation}`;
    const rows = document.getElementById("area-sample-rows");
    areaSampleLayers = point.sample_values.map(sample => {
      const valid = Number.isFinite(sample.frequency_pct);
      const probability = valid ? `${formatHazardNumber(sample.frequency_pct)}%` : "Unavailable";
      const row = document.createElement("tr");
      for (const value of [sample.latitude.toFixed(6), sample.longitude.toFixed(6), probability, sample.method]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      rows.append(row);
      return L.circleMarker([sample.latitude, sample.longitude], { radius: 5, color: valid ? "#2563eb" : "#64748b", fillOpacity: 0.8 }).addTo(map)
        .bindPopup(`Sample: ${probability} at ${sample.latitude.toFixed(6)}, ${sample.longitude.toFixed(6)} (${sample.method})`);
    });
    triggerExplanation.textContent = `${type.name}: average sampled exceedance for ${formatHazardNumber(level.severity)} ${data.unit || type.unit}.`;
  }
}

function clearAreaSamples() {
  for (const layer of areaSampleLayers) map.removeLayer(layer);
  areaSampleLayers = [];
  document.getElementById("area-average").hidden = true;
  document.getElementById("area-sample-details").hidden = true;
  document.getElementById("area-sample-rows").replaceChildren();
  document.getElementById("export-area").disabled = true;
}

triggerSelect.addEventListener("change", renderTriggerResult);
triggerPayout.addEventListener("input", renderPoolContribution);

function resetHazardView(message) {
  resetTrigger(message, selectedPoint ? "loading" : "idle");
  hazardStatus.textContent = message;
  hazardStatus.dataset.state = "loading";
  hazardChart.replaceChildren();
  hazardRows.replaceChildren();
  hazardSource.replaceChildren();
  hazardNotes.replaceChildren();
  intensityData = null;
  historicalHeatData = null;
  potentialFireData = null;
  document.getElementById("fire-history-details").hidden = true;
  document.getElementById("fire-history-rows").replaceChildren();
  document.getElementById("export-potential-fire").disabled = true;
  document.getElementById("heat-history-details").hidden = true;
  document.getElementById("heat-history-rows").replaceChildren();
  document.getElementById("export-heat").disabled = true;
  document.getElementById("intensity-value").textContent = "—";
  document.getElementById("intensity-preview-value").textContent = "—";
  document.getElementById("intensity-description").textContent = "";
  document.getElementById("export-intensity").disabled = true;
}

function configureDisasterView(peril) {
  const type = disasterTypes[peril];
  document.getElementById("potential-fire-prototype").hidden = peril !== "potential_fire_intensity";
  const intensity = peril === "fireline_intensity";
  document.getElementById("heat-observation-panel").hidden = peril !== "heat";
  document.getElementById("trigger-heading").textContent = intensity ? "Explore fireline intensity" : "Set your trigger and payout";
  document.getElementById("hazard-selection-description").textContent = intensity
    ? "Select a Texas location to sample the native fireline-intensity model."
    : "Use a government model’s supported levels for your selected location.";
  if (peril === "heat") document.getElementById("hazard-selection-description").textContent = "Select a point to load historical annual heat exceedance from a qualifying NOAA station. First lookup downloads the historical station inventory.";
  if (peril === "potential_fire_intensity") {
    const description = document.getElementById("hazard-selection-description");
    description.textContent = "Select a vegetated point to calculate potential fire intensity from 1991–2020 hourly weather. First lookup may take several minutes; source data are cached. This model supports U.S. LANDFIRE regions. ";
    const example = document.createElement("a");
    example.href = "/?peril=potential_fire_intensity&lat=30.08&lon=-97.3";
    example.textContent = "Open the prepared Bastrop example";
    description.append(example);
  }
  document.getElementById("intensity-preview").hidden = !intensity;
  document.getElementById("hazard-intensity").hidden = !intensity;
  document.getElementById("hazard-curve-layout").hidden = intensity;
  document.getElementById("hazard-axis-note").hidden = intensity;
  for (const id of ["trigger-level-control", "trigger-payout-control", "payout-help", "trigger-result", "comparison-panel"]) {
    document.getElementById(id).hidden = intensity;
  }
  document.getElementById("export-area").hidden = intensity || !selectedArea;
  resolvedDisaster = peril === "auto" ? null : peril;
  document.getElementById("flood-panel").hidden = peril !== "flood";
  hazardPanel.hidden = peril === "flood";
  document.getElementById("site-class-control").hidden = peril !== "earthquake";
  document.getElementById("metric-summary").textContent = peril === "auto" ? "Choose most relevant · Select a location to compare hazards" : `${disasterSelect.value === "auto" ? "Automatically selected: " : ""}${type.name} · ${type.metric} (${type.unit}) · ${type.source}`;
  document.getElementById("location-note").textContent = type.note;
  document.getElementById("hazard-heading").textContent = `${type.name} ${intensity ? "hazard" : "hazard curve"}`;
  document.getElementById("severity-column").textContent = `${type.metric} (${type.unit})`;
  document.getElementById("frequency-column").textContent = "Annual exceedance (%)";
  document.getElementById("coverage-column").hidden = true;
  document.getElementById("hazard-axis-note").textContent = `X: annual frequency (%) · Y: ${type.metric} (${type.unit})`;
  document.getElementById("map").setAttribute("aria-label", `Choose a location for ${type.name} hazard data`);
}

function resetRelevance(message) {
  relevancePanel.hidden = disasterSelect.value !== "auto";
  relevanceStatus.textContent = message;
  relevanceReason.textContent = "";
  relevanceSource.replaceChildren();
  relevanceChecks.replaceChildren();
  document.getElementById("relevance-details").hidden = true;
}

function showRelevance(selection, chosenPeril) {
  relevanceStatus.textContent = chosenPeril === "auto" ? "No usable local curve found." : `Automatically selected: ${disasterTypes[chosenPeril].name}`;
  relevanceReason.textContent = selection.explanation;
  const county = selection.county;
  if (county) {
    relevanceSource.append(`Regional context: ${county.name}, ${county.state} · FEMA NRI ${county.model_version || ""}. `);
    const link = document.createElement("a");
    link.href = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer/0";
    link.textContent = "Government ranking source";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    relevanceSource.append(link);
  } else {
    relevanceSource.textContent = "FEMA regional ranking unavailable. Selection uses available local curve data.";
  }
  for (const check of selection.checks || []) {
    const item = document.createElement("li");
    item.textContent = `${disasterTypes[check.peril].name}: ${check.reason}`;
    relevanceChecks.append(item);
  }
  document.getElementById("relevance-details").hidden = !relevanceChecks.children.length;
}

function applyDisasterSelection() {
  configureDisasterView(disasterSelect.value);
  resetRelevance("Select a location to choose a disaster automatically.");
  resetHazardView("Select a location to load government data.");
  if (selectedPoint) selectLocation(selectedPoint);
  else status.textContent = disasterSelect.value === "auto" ? "Select a location to find a relevant disaster with government data." : `Select a location to view ${disasterTypes[disasterSelect.value].name.toLowerCase()} data.`;
}

disasterSelect.addEventListener("change", applyDisasterSelection);
siteClassSelect.addEventListener("change", () => {
  if (selectedPoint && resolvedDisaster === "earthquake") selectLocation(selectedPoint);
});

function waitForFireProgress(signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(Object.assign(new Error("Selection changed"), {name: "AbortError"}));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 1000);
    signal.addEventListener("abort", abort, {once: true});
    if (signal.aborted) abort();
  });
}

async function loadSelectedHazard(lat, lng, version, preferredSeverity = null) {
  const peril = disasterSelect.value;
  let type = disasterTypes[peril];
  const controller = new AbortController();
  pendingHazardRequest = controller;
  resetHazardView(peril === "auto" ? "Comparing regional hazards and checking local government curves…" : `Loading ${type.source} data for the selected location…`);
  status.textContent = peril === "auto" ? "Choosing a relevant disaster for this location…" : `Loading ${type.name.toLowerCase()} model…`;
  if (peril === "potential_fire_intensity") {
    status.textContent = "Preparing 30 years of hourly potential fire behavior; first lookup may take several minutes…";
    hazardStatus.textContent = "Loading historical weather and fuel inputs, then calculating annual maxima. This measures potential conditions, not actual fire arrival.";
  }
  try {
    const params = new URLSearchParams({ peril, lat: String(lat), lon: String(lng), site_class: siteClassSelect.value });
    if (selectedArea) {
      params.set("south", selectedArea.getSouth());
      params.set("west", selectedArea.getWest());
      params.set("north", selectedArea.getNorth());
      params.set("east", selectedArea.getEast());
      status.textContent = peril === "fireline_intensity" ? "Sampling native fireline intensity at 25 grid points…" : "Calculating hazard curves at 25 grid points and averaging their probabilities…";
      if (peril === "heat") status.textContent = "Historical heat currently supports Point mode; checking selection…";
    }
    const backgroundFire = peril === "potential_fire_intensity" && !selectedArea;
    const requestUrl = backgroundFire
      ? `/api/potential-fire?${new URLSearchParams({lat: String(lat), lon: String(lng)})}`
      : `${selectedArea ? "/api/disaster-area" : "/api/disaster"}?${params}`;
    let response = await fetch(requestUrl, { signal: controller.signal });
    while (backgroundFire && response.status === 202) {
      const preparation = await response.json();
      if (controller.signal.aborted || version !== locationVersion) return;
      const progress = preparation.progress || {};
      const message = progress.phase === "queued" ? "Waiting to prepare this location…"
        : progress.phase === "landscape" ? "Checking fuel, canopy and terrain inputs…"
        : `Preparing historical weather and fire behavior: ${progress.completed_years ?? 0} / ${progress.total_years ?? 30} years ready.`;
      hazardStatus.textContent = message + " Completed years are saved; the curve appears when the baseline is complete.";
      status.textContent = message;
      await waitForFireProgress(controller.signal);
      response = await fetch(requestUrl, { signal: controller.signal });
    }
    if (controller.signal.aborted || version !== locationVersion) return;
    if (selectedArea && response.status === 404) {
      const message = "Area lookup is missing from the running server. Restart the Python server with the updated server.py, then draw the area again.";
      hazardStatus.dataset.state = "error";
      hazardStatus.textContent = message;
      status.textContent = message;
      resetTrigger(message, "error");
      return;
    }
    const data = await response.json();
    if (controller.signal.aborted || version !== locationVersion) return;
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (!Array.isArray(data.points) || (peril === "auto" ? !Object.hasOwn(disasterTypes, data.peril) || !data.selection : data.peril !== peril)) throw new Error("Unexpected government curve response");
    if (selectedArea && data.area?.aggregation !== "arithmetic_mean") {
      const message = "Area averages require the updated backend. Restart the Python server, then draw the area again.";
      hazardStatus.dataset.state = "error";
      hazardStatus.textContent = message;
      status.textContent = message;
      resetTrigger(message, "error");
      return;
    }
    if (peril === "auto") {
      configureDisasterView(data.peril);
      showRelevance(data.selection, data.peril);
      type = disasterTypes[data.peril];
      marker?.bindPopup(`${type.name} (automatic): ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    }
    loadTriggerLevels(data);
    if (data.peril === "heat") {
      renderHistoricalHeat(data);
      if (data.area) areaSummary.textContent = data.reason;
    }
    if (data.peril === "potential_fire_intensity") renderPotentialFireHistory(data);
    if (data.area && !["fireline_intensity", "heat", "potential_fire_intensity"].includes(data.peril)) {
      document.getElementById("hazard-heading").textContent = `${type.name} · average sampled exceedance curve`;
      hazardPanel.hidden = false;
      document.getElementById("coverage-column").hidden = false;
      areaSummary.textContent = `Selected area centroid: ${data.area.centroid.latitude.toFixed(6)}, ${data.area.centroid.longitude.toFixed(6)}. ${data.area.available_samples || 0}/${data.area.sample_count} grid locations have usable curves. Each curve point averages available probabilities at the same severity. Change the trigger to inspect all sample values below. This uses a 5×5 grid inside and on the boundary, not every raster cell. Automatic hazard selection uses county context at the area centroid.`;
      if (triggerLevels.length) {
        let index = triggerLevels.findIndex(level => level.severity === preferredSeverity);
        if (index < 0) {
          index = triggerLevels.reduce((best, level, i, levels) => level.maximum > levels[best].maximum ? i : best, 0);
          triggerHelp.textContent += " Initially selected the level with the highest average probability; you can change it.";
        }
        triggerSelect.value = String(index);
        renderTriggerResult();
      }
    }
    if (data.peril === "flood") {
      // Keep the trigger tied to unedited government data, independently of
      // manual/example values in the exploratory flood-damage table.
      loadTwdbDepths(lat, lng, version);
      loadFemaLocation(lat, lng, version);
      if (!data.area) return;
      document.getElementById("location-note").textContent += " Flood-zone context and the flood depth/damage table below describe the area centroid only. The trigger and area curve use the average of available grid-point probabilities.";
    }
    const severityLabel = `${data.metric || type.metric} (${data.unit || type.unit})`;
    const frequencyLabel = `${data.area && data.peril !== "heat" ? "Average sampled " : ""}${data.frequency_label || (data.peril === "heat" ? "Observed annual exceedance frequency, 1991–2020 (%)" : "Annual exceedance probability (%)")}`;
    document.getElementById("hazard-axis-note").textContent = `X: ${frequencyLabel} · Y: ${severityLabel}. ` + (data.peril === "potential_fire_intensity" ? "Modeled annual maxima on a fixed landscape. Frequencies describe potential conditions; connecting lines are not fitted probabilities." : data.peril === "heat" ? "Observed annual maxima only. Connecting lines guide the eye; trigger values are not interpolated or extrapolated." : "Lines guide the eye between supplied points.");
    document.getElementById("frequency-column").textContent = frequencyLabel;
    document.getElementById("severity-column").textContent = severityLabel;
    if (data.peril === "fireline_intensity") {
      renderFirelineIntensity(data);
    } else drawHazardChart(hazardChart, data.points, severityLabel, frequencyLabel);
    for (const point of data.points) {
      const row = document.createElement("tr");
      const frequency = document.createElement("td");
      const severity = document.createElement("td");
      frequency.textContent = `${formatHazardNumber(point.frequency_pct)}%`;
      severity.textContent = `${formatHazardNumber(point.severity)} ${data.unit || type.unit}`;
      row.append(frequency, severity);
      if (data.area) {
        const coverage = document.createElement("td");
        coverage.textContent = `${point.evaluated_samples}/${data.area.sample_count}`;
        row.append(coverage);
      }
      hazardRows.append(row);
    }
    for (const [label, value] of data.peril === "flood" ? [] : data.details || []) {
      const term = document.createElement("dt"), detail = document.createElement("dd");
      term.textContent = label;
      detail.textContent = value;
      results.append(term, detail);
    }
    results.hidden = !results.children.length;
    if (data.source_url && /^https:\/\//.test(data.source_url)) {
      const link = document.createElement("a");
      link.href = data.source_url;
      link.textContent = data.source_name || type.source;
      link.className = "source-link";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      hazardSource.append("Government source: ", link);
    }
    for (const note of data.notes || []) {
      const paragraph = document.createElement("p");
      paragraph.textContent = note;
      hazardNotes.append(paragraph);
    }
    for (const reference of data.source_references || []) {
      if (!/^https:\/\//.test(reference.url)) continue;
      const paragraph = document.createElement("p"), link = document.createElement("a");
      link.href = reference.url;
      link.textContent = reference.title;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      paragraph.append(link);
      hazardNotes.append(paragraph);
    }
    hazardStatus.dataset.state = data.status;
    hazardStatus.textContent = data.status === "available"
      ? (data.peril === "fireline_intensity" ? "Native fireline intensity loaded in kW/m. Annual exceedance probabilities are not supplied by this dataset." : `${data.points.length} severity–frequency points loaded${data.area ? " as averages across available grid samples. Coverage can differ by trigger." : " for this location."}`)
      : data.reason || "The government model has no data for this location.";
    status.textContent = data.status === "available" ? `${type.name} data loaded.` : "Model data unavailable at this location.";
  } catch (error) {
    if (error.name !== "AbortError" && version === locationVersion) {
      hazardStatus.dataset.state = "error";
      hazardStatus.textContent = `Could not load ${type.source}: ${error.message}`;
      resetTrigger(`Could not load ${type.source} trigger levels. Select the hazard or location again to retry.`, "error");
      status.textContent = "Government data lookup failed. Try another location or retry.";
      if (peril === "auto") relevanceStatus.textContent = "Automatic selection failed. Retry or select a disaster manually.";
    }
  } finally {
    if (pendingHazardRequest === controller) pendingHazardRequest = null;
  }
}

function renderPotentialFireHistory(data) {
  potentialFireData = Array.isArray(data.annual_maxima) && data.baseline ? data : null;
  document.getElementById("fire-history-details").hidden = !potentialFireData;
  document.getElementById("export-potential-fire").disabled = !potentialFireData;
  const rows = document.getElementById("fire-history-rows");
  rows.replaceChildren();
  if (!potentialFireData) return;
  document.getElementById("fire-history-context").textContent = `${data.baseline.complete_years}/30 complete years. Fixed ${data.landscape.fuel_vintage} fuel model ${data.landscape.fuel_model}; hourly ERA5 weather and ERA5-Land snow. Surface fire only, with fixed live moisture (herbaceous 60%, woody 90%). These values are modeled potential intensity, not measurements of historical fires.`;
  for (const year of data.annual_maxima) {
    const row = document.createElement("tr");
    for (const value of [year.year, `${year.valid_hours ?? 0} / ${year.expected_hours}`,
      Number.isFinite(year.maximum_kw_m) ? formatHazardNumber(year.maximum_kw_m) : "Unavailable",
      year.peak?.time_utc || "—", year.complete ? "Yes" : year.reason]) {
      const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell);
    }
    rows.append(row);
  }
}

document.getElementById("export-potential-fire").addEventListener("click", () => {
  const data = potentialFireData;
  if (!data) return;
  // Full JSON preserves raw-source URLs/hashes, peak model inputs and scenario metadata.
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url;
  anchor.download = "potential_surface_fire_1991_2020.json";
  document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
});

function renderHistoricalHeat(data) {
  historicalHeatData = data.station && data.baseline && Array.isArray(data.annual_maxima) ? data : null;
  document.getElementById("heat-history-details").hidden = !historicalHeatData;
  document.getElementById("export-heat").disabled = !historicalHeatData;
  const rows = document.getElementById("heat-history-rows");
  rows.replaceChildren();
  if (!historicalHeatData) return;
  const station = data.station;
  document.getElementById("heat-history-context").textContent = `${station.name} (${station.id}), ${station.distance_km.toFixed(2)} km away, at ${station.latitude.toFixed(5)}, ${station.longitude.toFixed(5)}. ${data.baseline.complete_years}/30 complete years in 1991–2020. ` +
    `Checked stations, nearest first: ${(data.station_selection?.checked || []).map(row => `${row.id}: ${row.complete_years} complete years${row.eligible ? " (qualifies)" : " (excluded)"}`).join("; ")}. Daily dates follow the source observing day; they are not UTC observation timestamps.`;
  for (const year of data.annual_maxima) {
    const row = document.createElement("tr");
    const maximum = Number.isFinite(year.maximum_c) ? `${formatHazardNumber(year.maximum_c)} / ${formatHazardNumber(year.maximum_c * 9 / 5 + 32)}` : "Unavailable";
    for (const value of [year.year, `${year.valid_days}/${year.expected_days}`, maximum, year.maximum_date || "—", year.complete ? "Yes" : "No — missing/flagged days"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    rows.append(row);
  }
}

document.getElementById("export-heat").addEventListener("click", () => {
  const data = historicalHeatData;
  if (!data) return;
  const header = ["row_type", "threshold_c", "frequency_pct", "hit_years", "included_years", "year", "valid_days", "expected_days", "annual_max_c", "annual_max_f", "maximum_date", "year_included", "station_id", "station_name", "station_latitude", "station_longitude", "distance_km", "baseline_start", "baseline_end", "source_url", "source_sha256", "fetched_at"];
  const source = data.provenance?.sources?.at(-1) || {};
  const common = [data.station.id, data.station.name, data.station.latitude, data.station.longitude, data.station.distance_km, data.baseline.start_year, data.baseline.end_year, data.source_url, source.sha256, source.fetched_at];
  const rows = [
    ...data.points.map(point => ["threshold", point.severity, point.frequency_pct, point.hit_years, point.exposure_years, "", "", "", "", "", "", "", ...common]),
    ...data.annual_maxima.map(year => ["annual_maximum", "", "", "", data.baseline.complete_years, year.year, year.valid_days, year.expected_days, year.maximum_c, Number.isFinite(year.maximum_c) ? year.maximum_c * 9 / 5 + 32 : null, year.maximum_date, year.complete, ...common]),
  ];
  const url = URL.createObjectURL(new Blob([[header, ...rows].map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `historical_heat_${data.station.id}_1991_2020.csv`;
  document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
});

function renderFirelineIntensity(data) {
  intensityData = data;
  const value = data.hazard_value?.value;
  document.getElementById("intensity-value").textContent = Number.isFinite(value) ? `${formatHazardNumber(value)} kW/m` : "Unavailable";
  document.getElementById("intensity-preview-value").textContent = document.getElementById("intensity-value").textContent;
  document.getElementById("intensity-preview-statistic").textContent = data.area
    ? `Spatial mean of available weather-weighted heading intensities (${data.area.available_samples}/${data.area.sample_count} samples).`
    : "Weather-weighted mean of modeled heading fire behavior.";
  document.getElementById("intensity-label").textContent = data.area ? "Average sampled fireline intensity" : "Weather-weighted fireline intensity";
  document.getElementById("intensity-description").textContent = data.area
    ? data.area.method
    : "Heat released per second per metre of fire front. Native modeled heading-fire intensity averaged using weather-type weights; no flame-length conversion.";
  document.getElementById("export-intensity").disabled = !Number.isFinite(value);
  if (!data.area) return;
  document.getElementById("export-area").hidden = true;
  const summary = data.area.summary;
  areaSummary.textContent = `${data.area.available_samples}/${data.area.sample_count} grid locations have native intensity values. ${Number.isFinite(value) ? `Sum ${formatHazardNumber(summary.sum)} kW/m ÷ ${data.area.available_samples} = ${formatHazardNumber(value)} kW/m. ` : ""}This is a mean of sampled intensities, not an annual probability or an area-wide maximum.`;
  document.getElementById("area-sample-details").hidden = false;
  document.getElementById("area-sample-value").textContent = "Fireline intensity (kW/m)";
  document.getElementById("area-sample-context").textContent = "Native intensity at each of the 25 grid locations; blue dots have data, gray dots are unavailable.";
  document.getElementById("area-sample-mean").textContent = areaSummary.textContent;
  const rows = document.getElementById("area-sample-rows");
  areaSampleLayers = data.area.samples.map(sample => {
    const valid = Number.isFinite(sample.value);
    const text = valid ? `${formatHazardNumber(sample.value)} kW/m` : "Unavailable";
    const row = document.createElement("tr");
    for (const textValue of [sample.latitude.toFixed(6), sample.longitude.toFixed(6), text, valid ? "Native nearest pixel" : "Unavailable"]) {
      const cell = document.createElement("td");
      cell.textContent = textValue;
      row.append(cell);
    }
    rows.append(row);
    return L.circleMarker([sample.latitude, sample.longitude], { radius: 5, color: valid ? "#2563eb" : "#64748b", fillOpacity: 0.8 }).addTo(map)
      .bindPopup(`Fireline intensity: ${text}`);
  });
}

document.getElementById("export-intensity").addEventListener("click", () => {
  if (!intensityData || !Number.isFinite(intensityData.hazard_value?.value)) return;
  const data = intensityData;
  const header = ["hazard", "row_type", "latitude", "longitude", "intensity_kw_per_m", "statistic", "source_url", "retrieved_at", "sample_sha256", "metadata_sha256", "annual_probability_available", "south", "west", "north", "east", "method"];
  const row = (kind, lat, lon, value, provenance = {}) => [data.peril, kind, lat, lon, value,
    kind === "sample" ? "weather_weighted_mean_heading_fireline_intensity" : data.hazard_value.statistic,
    data.source_url, provenance.retrieved_at, provenance.sample_sha256, provenance.metadata_sha256, false,
    data.area?.bounds.south, data.area?.bounds.west, data.area?.bounds.north, data.area?.bounds.east,
    data.area?.method || "Native nearest pixel; weather-weighted heading fireline intensity"];
  const rows = data.area ? [row("average", data.area.centroid.latitude, data.area.centroid.longitude, data.hazard_value.value),
    ...data.area.samples.map(s => row("sample", s.latitude, s.longitude, s.value, s.provenance || {}))]
    : [row("point", data.latitude, data.longitude, data.hazard_value.value, data.provenance)];
  const url = URL.createObjectURL(new Blob([[header, ...rows].map(r => r.map(csvCell).join(",")).join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "fireline_intensity.csv";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// These are event frequencies, not observed counts or a fixed schedule.
const scenarios = [
  { probability: 20, label: "5-year" },
  { probability: 10, label: "10-year" },
  { probability: 1, label: "100-year" },
  { probability: 0.2, label: "500-year" },
];
const scenarioRows = document.getElementById("scenario-rows");
const scenarioContext = document.getElementById("scenario-context");
const scenarioNote = document.getElementById("scenario-note");
const depthStatus = document.getElementById("depth-status");
const depthChart = document.getElementById("depth-chart");
const damageChart = document.getElementById("damage-chart");
const buildingNotes = document.getElementById("building-notes");
const buildingProfile = document.getElementById("building-profile");
const firstFloorHeight = document.getElementById("first-floor-height");
const autoDamage = document.getElementById("auto-damage");
const exportButton = document.getElementById("export-button");
const exportStatus = document.getElementById("export-status");
const searchForm = document.getElementById("address-search");
const addressInput = document.getElementById("address-input");
const searchButton = document.getElementById("search-button");
const searchStatus = document.getElementById("search-status");

for (const scenario of scenarios) {
  const row = document.createElement("tr");
  row.innerHTML = `<th scope="row">${scenario.probability}%</th><td>${scenario.label}</td>` +
    `<td><input type="number" min="0" step="0.01" aria-label="${scenario.probability}% flood depth in feet" class="depth-input"><small class="depth-detail">Not sampled</small></td>` +
    `<td><input type="number" min="0" max="100" step="0.1" aria-label="${scenario.probability}% damage percent" class="damage-input"><small class="damage-detail">Not entered</small></td>`;
  scenarioRows.append(row);
  scenario.depthInput = row.querySelector(".depth-input");
  scenario.depthDetail = row.querySelector(".depth-detail");
  scenario.damageInput = row.querySelector(".damage-input");
  scenario.damageDetail = row.querySelector(".damage-detail");
  scenario.depthInput.addEventListener("input", () => {
    pendingDepthRequest?.abort();
    scenario.depthDetail.textContent = "Edited manually";
    depthStatus.textContent = "Depth values have been edited manually for this point.";
    scenarioNote.textContent = "One or more depth values were edited manually; record those changes before using the curve.";
    calculateDamage();
  });
  scenario.damageInput.addEventListener("input", () => {
    scenario.damageDetail.textContent = "Entered manually";
    renderCharts();
  });
}

function calculateDamage() {
  for (const scenario of scenarios) {
    scenario.damageInput.readOnly = autoDamage.checked;
    if (!autoDamage.checked) continue;
    const curve = HAZUS_CURVES[buildingProfile.value];
    const height = readNumber(firstFloorHeight, 30);
    const depth = readNumber(scenario.depthInput);
    const damage = height === null || depth === null ? null : estimateStructureDamage(depth, height, curve);
    scenario.damageInput.value = damage === null ? "" : damage.toFixed(1);
    scenario.damageDetail.textContent = !curve || height === null ? "Choose profile and floor height"
      : depth === null ? "No mapped depth; estimate unavailable"
      : damage === null ? "Outside Hazus curve range"
      : `Hazus 4.0 function ${curve.id}; floor depth ${(depth - height).toFixed(2)} ft`;
  }
  renderCharts();
}

for (const input of [buildingProfile, firstFloorHeight, autoDamage]) {
  input.addEventListener("input", () => {
    if (!autoDamage.checked) {
      for (const scenario of scenarios) {
        scenario.damageInput.readOnly = false;
        if (scenario.damageDetail.textContent.startsWith("Hazus") || scenario.damageDetail.textContent.startsWith("Choose") || scenario.damageDetail.textContent.startsWith("No mapped") || scenario.damageDetail.textContent.startsWith("Outside")) {
          scenario.damageInput.value = "";
          scenario.damageDetail.textContent = "Not entered";
        }
      }
    }
    calculateDamage();
  });
}

function readNumber(input, maximum = Infinity) {
  if (input.value.trim() === "") return null;
  const value = Number(input.value);
  return Number.isFinite(value) && value >= 0 && value <= maximum ? value : null;
}

// Draw only supplied scenario points. The connecting line is visual, not interpolation.
function drawChart(container, values, yLabel, fixedMaximum) {
  const points = scenarios.map((scenario, index) => ({ frequency_pct: scenario.probability, severity: values[index] }));
  drawHazardChart(container, points, yLabel, "Annual exceedance probability (%)", fixedMaximum);
}

function renderCharts() {
  drawChart(depthChart, scenarios.map((s) => {
    const feet = readNumber(s.depthInput);
    return feet === null ? null : feet * 0.3048;
  }), "Peak inundation depth (m)");
  drawChart(damageChart, scenarios.map((s) => readNumber(s.damageInput, 100)), "Damage percent", 100);
}

function clearScenarios(message = "TWDB depths will appear after a map click if the local raster tile is installed.") {
  for (const scenario of scenarios) {
    scenario.depthInput.value = "";
    scenario.damageInput.value = "";
    scenario.depthDetail.textContent = "Not sampled";
    scenario.damageDetail.textContent = "Not entered";
  }
  renderCharts();
  scenarioNote.textContent = message;
}

document.getElementById("clear-button").addEventListener("click", () => {
  pendingDepthRequest?.abort();
  clearScenarios();
  depthStatus.textContent = "Values cleared. Click the map to sample this location again.";
});
document.getElementById("example-button").addEventListener("click", () => {
  pendingDepthRequest?.abort();
  autoDamage.checked = false;
  // Deliberately hypothetical values: they demonstrate the plot, not a real site.
  const exampleDepths = [0.2, 0.5, 1.6, 3.1];
  const exampleDamages = [0, 3, 18, 42];
  scenarios.forEach((scenario, index) => {
    scenario.depthInput.value = exampleDepths[index];
    scenario.damageInput.value = exampleDamages[index];
    scenario.damageInput.readOnly = false;
    scenario.damageDetail.textContent = "Invented example";
    scenario.depthDetail.textContent = "Invented example";
  });
  buildingNotes.value = "Hypothetical building and damage values for demonstration only";
  renderCharts();
  scenarioNote.textContent = "ILLUSTRATIVE EXAMPLE ONLY — these depths and damages are invented and do not describe the selected location.";
  depthStatus.textContent = "Illustrative example loaded; click the map to return to real TWDB sampling.";
});

function csvCell(value) {
  const string = value == null ? "" : String(value);
  return /[",\r\n]/.test(string) ? `"${string.replaceAll('"', '""')}"` : string;
}

document.getElementById("export-area").addEventListener("click", () => {
  if (!triggerData?.area || triggerSelect.value === "") return;
  const area = triggerData.area;
  const header = ["hazard", "severity", "unit", "row_type", "latitude", "longitude", "exceedance_pct", "sample_method", "frequency_basis",
    "south", "west", "north", "east", "area_centroid_latitude", "area_centroid_longitude",
    "evaluated_samples", "missing_samples", "interpolated_samples", "sum_frequency_pct", "method", "site_class"];
  const rows = [];
  for (const point of triggerData.points) {
    const context = [hazardTriggerBasis(triggerData.peril).label, area.bounds.south, area.bounds.west, area.bounds.north, area.bounds.east,
      area.centroid.latitude, area.centroid.longitude, point.evaluated_samples, point.missing_samples, point.interpolated_samples,
      point.sum_frequency_pct, area.method, triggerData.peril === "earthquake" ? siteClassSelect.value : ""];
    rows.push([triggerData.peril, point.severity, triggerData.unit, "average", "", "", point.frequency_pct, "arithmetic_mean", ...context]);
    for (const sample of point.sample_values) {
      rows.push([triggerData.peril, point.severity, triggerData.unit, "sample", sample.latitude, sample.longitude,
        sample.frequency_pct, sample.method, ...context]);
    }
  }
  const url = URL.createObjectURL(new Blob([[header, ...rows].map(r => r.map(csvCell).join(",")).join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `area_average_${triggerData.peril}.csv`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

exportButton.addEventListener("click", () => {
  if (!selectedPoint) return;
  const curve = autoDamage.checked ? HAZUS_CURVES[buildingProfile.value] : null;
  const header = ["latitude", "longitude", "annual_chance_pct", "common_event_label", "twdb_depth_ft", "depth_status", "damage_pct_structure", "damage_method", "hazus_function_id", "building_profile", "first_floor_height_ft", "fema_fld_zone", "fema_zone_subty", "fema_sfha_tf", "fema_static_bfe", "fema_depth", "notes", "selection_type", "area_south", "area_west", "area_north", "area_east", "sampling_method"];
  const rows = scenarios.map((scenario) => [selectedPoint.lat.toFixed(6), selectedPoint.lng.toFixed(6), scenario.probability, scenario.label,
    scenario.depthInput.value, scenario.depthDetail.textContent, scenario.damageInput.value,
    curve && scenario.damageInput.value ? "FEMA Hazus 4.0 riverine RES1" : scenario.damageInput.value ? "Manual / illustrative" : "",
    curve?.id ?? "", buildingProfile.value, firstFloorHeight.value,
    femaAttributes?.FLD_ZONE, femaAttributes?.ZONE_SUBTY, femaAttributes?.SFHA_TF,
    femaAttributes?.STATIC_BFE, femaAttributes?.DEPTH, buildingNotes.value,
    selectedArea ? "area" : "point", selectedArea?.getSouth(), selectedArea?.getWest(),
    selectedArea?.getNorth(), selectedArea?.getEast(), selectedArea ? "Center point only; not an area aggregate" : "Selected point"]);
  const csv = [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `flood_${selectedArea ? "area_center" : "point"}_${selectedPoint.lat.toFixed(4)}_${selectedPoint.lng.toFixed(4)}.csv`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  exportStatus.textContent = `CSV download requested: ${anchor.download}`;
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

clearScenarios();

async function loadTwdbDepths(lat, lng, version) {
  pendingDepthRequest?.abort();
  const controller = new AbortController();
  pendingDepthRequest = controller;
  depthStatus.textContent = "Sampling TWDB 2025 existing-conditions depths…";
  try {
    const params = new URLSearchParams({ lat: String(lat), lon: String(lng) });
    const response = await fetch(`/api/depths?${params}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (controller.signal.aborted || version !== locationVersion) return;
    const labels = {
      sampled: "TWDB raster",
      no_mapped_depth: "No mapped depth / NoData",
      not_installed: "No local TWDB tile",
      read_error: "Raster read error",
    };
    let sampled = 0;
    let missingTiles = 0;
    for (const scenario of scenarios) {
      const item = data.events?.[String(scenario.probability)];
      if (!item || !Object.hasOwn(labels, item.status)) throw new Error("Unexpected depth response");
      scenario.depthInput.value = item.status === "sampled" ? String(Number(item.depth_ft.toFixed(2))) : "";
      scenario.depthDetail.textContent = labels[item.status];
      if (item.status === "sampled") sampled++;
      if (item.status === "not_installed") missingTiles++;
    }
    calculateDamage();
    depthStatus.textContent = missingTiles === scenarios.length
      ? `No local TWDB depth tile for ${data.tile}. TWDB covers Texas only. For a Texas point, run python prepare_data.py --tile ${data.tile}, then click again.`
      : sampled
        ? `TWDB depths sampled for tile ${data.tile}: ${sampled} of 4 events have mapped depth.`
        : `No positive modeled depths were returned for tile ${data.tile}. Check the row labels for missing tiles or NoData.`;
    scenarioNote.textContent = "Source: TWDB 2025 existing conditions (scenario 5), combined-peril 3 m depth rasters; depth in feet. Zero/NoData is left blank. Structure damage uses a Hazus 4.0 function only when enabled and the building profile is complete.";
  } catch (error) {
    if (error.name !== "AbortError" && version === locationVersion) {
      depthStatus.textContent = "Could not load TWDB depths. Run the local Python server and check that the raster tiles are installed.";
      console.error("TWDB depth lookup failed:", error);
    }
  } finally {
    if (pendingDepthRequest === controller) pendingDepthRequest = null;
  }
}

async function selectLocation(latlng) {
  const { lat, lng } = latlng;
  const preferredSeverity = triggerSelect.value === "" ? null : triggerLevels[Number(triggerSelect.value)]?.severity ?? null;
  pendingRequest?.abort();
  pendingDepthRequest?.abort();
  pendingHazardRequest?.abort();
  clearStation();
  const version = ++locationVersion;
  configureDisasterView(disasterSelect.value);
  if (disasterSelect.value === "auto") {
    resetRelevance("Comparing regional hazards and checking local curves…");
    resetHazardView("Choosing a disaster for the selected location…");
  }
  selectedPoint = { lat, lng };
  exportStatus.textContent = "";
  femaAttributes = null;
  exportButton.disabled = false;
  if (marker) marker.setLatLng(latlng);
  else marker = L.marker(latlng).addTo(map);

  coordinates.textContent = `${selectedArea ? "Area center · " : ""}Latitude: ${lat.toFixed(6)}  ·  Longitude: ${lng.toFixed(6)}`;
  scenarioContext.textContent = `${selectedArea ? "Area center sample (not an area aggregate)" : "Scenario point"}: ${lat.toFixed(6)}, ${lng.toFixed(6)}.`;
  exportButton.textContent = selectedArea ? "Export area center CSV" : "Export selected point CSV";
  clearScenarios();
  results.hidden = true;
  results.replaceChildren();
  marker.bindPopup(`${disasterTypes[disasterSelect.value].name}: ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
  loadSelectedHazard(lat, lng, version, preferredSeverity);
  loadNearestStations(lat, lng, version);
}

async function loadFemaLocation(lat, lng, version) {
  status.textContent = "Looking up FEMA flood hazard data…";

  // Cancel the previous lookup so a slower response cannot overwrite a newer click.
  pendingRequest?.abort();
  const controller = new AbortController();
  pendingRequest = controller;

  // ArcGIS REST expects point coordinates as longitude,latitude in WGS 84 (EPSG:4326).
  const params = new URLSearchParams({
    f: "json",
    where: "1=1",
    geometry: `${lng},${lat}`,
    geometryType: "esriGeometryPoint",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields: fields.map(([name]) => name).join(","),
    returnGeometry: "false",
  });

  try {
    const response = await fetch(`${FEMA_QUERY_URL}?${params}`, {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    if (controller.signal.aborted || version !== locationVersion) return;
    // ArcGIS can report service errors inside an otherwise successful HTTP response.
    if (data.error) throw new Error(data.error.message || "FEMA service error");
    if (!Array.isArray(data.features)) throw new Error("Unexpected FEMA response");

    const attributes = data.features[0]?.attributes;
    if (!attributes) {
      status.textContent = "No mapped flood hazard found at this location";
      return;
    }
    femaAttributes = attributes;

    for (const [name, label] of fields) {
      const term = document.createElement("dt");
      const value = document.createElement("dd");
      term.textContent = label;
      const rawValue = attributes[name];
      const isMissingNumber = ["STATIC_BFE", "DEPTH"].includes(name) &&
        (rawValue === -9999 || rawValue === -8888);
      value.textContent = isMissingNumber
        ? `Not provided (FEMA code ${rawValue})`
        : rawValue === null || rawValue === undefined || rawValue === ""
          ? "Not provided"
          : String(rawValue);
      results.append(term, value);
    }
    status.textContent = "FEMA flood hazard polygon found.";
    results.hidden = false;
  } catch (error) {
    if (error.name !== "AbortError" && version === locationVersion) {
      status.textContent = "Could not load FEMA data. Check your connection and try another click.";
      console.error("FEMA lookup failed:", error);
    }
  } finally {
    if (pendingRequest === controller) pendingRequest = null;
  }
}

function finishDrawing() {
  areaStart = null;
  drawingArea = false;
  if (areaPreview) map.removeLayer(areaPreview);
  areaPreview = null;
  cancelArea.hidden = true;
  document.getElementById("map").classList.remove("selecting-area");
  map.doubleClickZoom.enable();
}

function beginArea() {
  finishDrawing();
  pendingGeocodeRequest?.abort();
  drawingArea = true;
  cancelArea.hidden = false;
  document.getElementById("map").classList.add("selecting-area");
  map.doubleClickZoom.disable();
  selectionHelp.textContent = disasterSelect.value === "fireline_intensity"
    ? "Click two opposite corners to draw an area. Average native fireline intensity across a 5×5 grid. Press Escape to cancel."
    : "Click two opposite corners to draw an area. Compare exceedance across a 5×5 grid. Press Escape to cancel.";
}

function clearArea() {
  clearAreaSamples();
  document.getElementById("export-area").hidden = true;
  finishDrawing();
  if (areaLayer) map.removeLayer(areaLayer);
  areaLayer = null;
  selectedArea = null;
  areaSummary.hidden = true;
}

selectionMode.addEventListener("change", () => {
  redrawArea.hidden = selectionMode.value !== "area";
  if (selectionMode.value === "area") beginArea();
  else {
    clearArea();
    selectionHelp.textContent = "Click the map to select a point.";
    if (selectedPoint) selectLocation(selectedPoint);
  }
});
redrawArea.addEventListener("click", beginArea);
cancelArea.addEventListener("click", () => {
  finishDrawing();
  selectionHelp.textContent = selectedArea ? "Area kept. Choose Draw new area to replace it." : "Choose Draw new area to select an area.";
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawingArea) cancelArea.click();
});
map.on("mousemove", ({ latlng }) => {
  if (drawingArea && areaStart) areaPreview.setBounds(L.latLngBounds(areaStart, latlng));
});
map.on("click", ({ latlng }) => {
  pendingGeocodeRequest?.abort();
  if (selectionMode.value === "area") {
    if (!drawingArea) return;
    if (!areaStart) {
      areaStart = latlng;
      areaPreview = L.rectangle(L.latLngBounds(latlng, latlng), { color: "#2563eb", dashArray: "6 4", interactive: false }).addTo(map);
      selectionHelp.textContent = "Click the opposite corner to finish the area. Press Escape to cancel.";
      return;
    }
    const bounds = L.latLngBounds(areaStart, latlng);
    if (bounds.getNorth() - bounds.getSouth() < 0.00001 || bounds.getEast() - bounds.getWest() < 0.00001) {
      selectionHelp.textContent = "Choose a different opposite corner so the area has width and height.";
      return;
    }
    finishDrawing();
    if (areaLayer) map.removeLayer(areaLayer);
    selectedArea = bounds;
    document.getElementById("export-area").hidden = disasterSelect.value === "fireline_intensity";
    areaLayer = L.rectangle(bounds, { color: "#2563eb", fillOpacity: 0.12, interactive: false }).addTo(map);
    areaSummary.hidden = false;
    areaSummary.textContent = `Selected area: south ${bounds.getSouth().toFixed(5)}, west ${bounds.getWest().toFixed(5)}, north ${bounds.getNorth().toFixed(5)}, east ${bounds.getEast().toFixed(5)}. Loading 25 ${disasterSelect.value === "fireline_intensity" ? "native fireline-intensity samples" : "samples to compare exceedance probabilities"}…`;
    selectionHelp.textContent = "Area selected. Choose Draw new area to replace it, or Point to select a single location.";
    selectLocation(bounds.getCenter());
    return;
  }
  selectLocation(latlng);
});

applyDisasterSelection();

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const address = addressInput.value.trim();
  if (!address) return;
  if (!validStationCount()) {
    stationStatus.textContent = "n must be a positive integer from 1 to 50.";
    return;
  }
  clearStation();
  stationStatus.textContent = "Waiting for a unique address match…";
  pendingGeocodeRequest?.abort();
  const controller = new AbortController();
  pendingGeocodeRequest = controller;
  searchButton.disabled = true;
  searchStatus.textContent = "Searching address…";
  try {
    const params = new URLSearchParams({ address });
    const response = await fetch(`/api/geocode?${params}`, { signal: controller.signal });
    const data = await response.json();
    if (controller.signal.aborted || pendingGeocodeRequest !== controller) return;
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (data.status === "no_match" || data.status === "ambiguous") {
      searchStatus.textContent = data.message || "No unique address match found. Include street number, city, state and ZIP code, or click the map.";
      stationStatus.textContent = "Stations were not searched: enter a unique address or select a point on the map.";
      return;
    }
    if (data.status !== "matched" || !Number.isFinite(data.latitude) || !Number.isFinite(data.longitude)) {
      throw new Error("Unexpected address response");
    }
    const point = L.latLng(data.latitude, data.longitude);
    clearArea();
    selectionMode.value = "point";
    redrawArea.hidden = true;
    selectionHelp.textContent = "Address selected as a point. Choose Rectangular area to draw an area nearby.";
    map.setView(point, 15);
    searchStatus.textContent = `Matched: ${data.matched_address}. Approximate address point; verify on the map.`;
    selectLocation(point);
  } catch (error) {
    if (error.name !== "AbortError") {
      searchStatus.textContent = "Address search failed. Check your connection or click the map instead.";
      stationStatus.textContent = "Stations were not searched because address lookup failed.";
      console.error("Address search failed:", error);
    }
  } finally {
    if (pendingGeocodeRequest === controller) {
      pendingGeocodeRequest = null;
      searchButton.disabled = false;
    }
  }
});

// Reproducible point links, including the prepared scientific example.
const sharedSelection = new URLSearchParams(globalThis.location?.search || "");
if (["peril", "lat", "lon"].every(key => sharedSelection.getAll(key).length === 1 && sharedSelection.get(key).trim())) {
  const peril = sharedSelection.get("peril");
  const lat = Number(sharedSelection.get("lat")), lng = Number(sharedSelection.get("lon"));
  if (peril !== "fireline_intensity" && Object.hasOwn(disasterTypes, peril) && Number.isFinite(lat) && Number.isFinite(lng) &&
      lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180) {
    disasterSelect.value = peril;
    const point = L.latLng(lat, lng);
    map.setView(point, 12);
    selectLocation(point);
  }
}
