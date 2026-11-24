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
// Only the stations relevant to the selected hazard are shown. METAR weather reports are
// fetched only for Heat, where they feed the current heat screening panel.
let pendingStationRequest;
const stationCount = document.getElementById("station-count");
const heatStatus = document.getElementById("heat-status");
const heatList = document.getElementById("heat-list");
document.getElementById("heat-refresh").addEventListener("click", () => {
  if (selectedPoint) loadHeatStations(selectedPoint.lat, selectedPoint.lng, locationVersion);
});

function stationPeril() {
  return resolvedDisaster || disasterSelect.value;
}

function refreshStations() {
  if (!selectedPoint) return;
  const peril = stationPeril();
  if (peril === "heat") {
    loadHeatStations(selectedPoint.lat, selectedPoint.lng, locationVersion);
  } else {
    loadHazardStation(selectedPoint.lat, selectedPoint.lng, locationVersion, peril);
  }
  // The monitor table follows "Stations (n)" for every hazard that has one.
  if (!selectedArea && recordsConfig && RECORD_KINDS[peril]) loadStationRecords(peril, selectedPoint.lat, selectedPoint.lng, locationVersion);
}
stationCount.addEventListener("input", refreshStations);

function validStationCount() {
  return /^[0-9]+$/.test(stationCount.value) && Number(stationCount.value) >= 1 && Number(stationCount.value) <= 50;
}

function clearStation() {
  pendingStationRequest?.abort();
  pendingStationRequest = null;
  heatList.replaceChildren();
  heatStatus.textContent = "Heat screening cleared. Select a location or refresh station reports.";
}

async function loadHeatStations(lat, lng, version) {
  clearStation();
  if (!validStationCount()) {
    heatStatus.textContent = "Heat screening unavailable: station count must be an integer from 1 to 50.";
    return;
  }
  const controller = new AbortController();
  pendingStationRequest = controller;
  const current = () => !controller.signal.aborted && version === locationVersion && pendingStationRequest === controller;
  const timeout = setTimeout(() => {
    if (!current()) return;
    controller.abort();
    heatStatus.textContent = "Heat screening unavailable: station request timed out. Refresh to retry.";
  }, 15000);
  heatStatus.textContent = "Loading station observation snapshot for heat screening…";
  try {
    const params = new URLSearchParams({ network: "awc_metar", n: stationCount.value, lat, lon: lng });
    const response = await fetch(`/api/v1/monitoring-stations?${params}`, { signal: controller.signal });
    if (!current()) return;
    if (response.status === 404) throw new Error("Restart the Python server to enable monitoring-station lookup.");
    const payload = await response.json();
    if (!current()) return;
    if (!response.ok) throw new Error(payload.error?.message || "Monitoring-station lookup failed.");
    if (!payload.data || !Array.isArray(payload.data.stations)) throw new Error("Unexpected station response. Restart the Python server and retry.");
    renderHeatStations(payload.data.stations);
  } catch (error) {
    if (current()) heatStatus.textContent = "Heat screening unavailable: station service failed. Refresh to retry.";
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

let pendingHazardStationRequest;
let hazardStationMarkers = [];
let hazardStationPoints = [];
const hazardStationStatus = document.getElementById("hazard-station-status");
const hazardStationDetails = document.getElementById("hazard-station-details");
const hazardStationShow = document.getElementById("hazard-station-show");
const hazardStationRetry = document.getElementById("hazard-station-retry");
const hazardStationNetworks = {
  flood: "USGS stream gauges",
  rainfall: "USGS precipitation gauges",
  earthquake: "EarthScope seismic stations",
  nearest_fire: "NIFC RAWS fire-weather stations",
  satellite_fire: "NIFC RAWS fire-weather stations",
  fireline_intensity: "NIFC RAWS fire-weather stations",
  cyclone: "NOAA METAR wind stations",
};

function clearHazardStation() {
  pendingHazardStationRequest?.abort();
  pendingHazardStationRequest = null;
  hazardStationMarkers.forEach(layer => map.removeLayer(layer));
  hazardStationMarkers = [];
  hazardStationPoints = [];
  hazardStationDetails.replaceChildren();
  hazardStationShow.hidden = true;
  hazardStationRetry.hidden = true;
}

function renderHazardStations(stations, network, message, summary = network) {
  hazardStationDetails.replaceChildren();
  hazardStationStatus.textContent = stations.length ? summary : `No eligible station found. ${network}`;
  if (stations.length) {
    const list = document.createElement("ol");
    for (const [index, station] of stations.entries()) {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = `${station.name} (${station.id}) · ${station.distance_km.toFixed(2)} km`;
      const position = document.createElement("p");
      position.textContent = `Coordinates: ${station.latitude.toFixed(5)}, ${station.longitude.toFixed(5)}.`;
      item.append(title, position);
      try {
        const url = new URL(station.url);
        if (url.protocol === "https:" && ["waterdata.usgs.gov", "service.earthscope.org", "services3.arcgis.com", "www.weather.gov", "aviationweather.gov", "www.ncei.noaa.gov"].includes(url.hostname)) {
          const link = document.createElement("a");
          link.href = url.href;
          link.textContent = `View ${station.id} station data`;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          item.append(link);
        }
      } catch { /* A missing or invalid provider URL does not hide the station. */ }
      list.append(item);
      const point = {lat: station.latitude, lng: station.longitude};
      const popup = document.createElement("span");
      popup.textContent = `${network}: ${index + 1}. ${title.textContent}`;
      hazardStationMarkers.push(L.circleMarker(point, {radius: index ? 9 : 13, color: "#7e22ce", weight: 3, fillColor: "#c084fc", fillOpacity: 0.8}).addTo(map).bindPopup(popup));
      hazardStationPoints.push(point);
    }
    const legend = document.createElement("p");
    legend.textContent = "Purple markers show these stations; the largest is the nearest.";
    hazardStationDetails.append(list, legend);
    hazardStationShow.hidden = false;
  }
  const note = document.createElement("p");
  note.textContent = message;
  hazardStationDetails.append(note);
}

async function loadHazardStation(lat, lng, version, peril) {
  clearHazardStation();
  document.getElementById("hazard-station-heading").textContent = `Nearest monitoring stations · ${disasterTypes[peril].name}`;
  if (peril === "auto") {
    hazardStationStatus.textContent = "Waiting for automatic hazard selection…";
    return;
  }
  if (peril === "heat") {
    hazardStationStatus.textContent = "Waiting for the qualifying GHCN-Daily historical heat station…";
    return;
  }
  const network = hazardStationNetworks[peril];
  if (!network) return;
  if (!validStationCount()) {
    hazardStationStatus.textContent = "n must be a positive integer from 1 to 50.";
    return;
  }
  const count = stationCount.value;
  hazardStationStatus.textContent = `Finding the nearest ${count} ${count === "1" ? "station" : "stations"} in ${network}…`;
  const controller = new AbortController();
  pendingHazardStationRequest = controller;
  const current = () => !controller.signal.aborted && version === locationVersion && pendingHazardStationRequest === controller;
  const timeout = setTimeout(() => {
    if (!current()) return;
    controller.abort();
    hazardStationStatus.textContent = `${network} search timed out. Retry the hazard station search.`;
    hazardStationRetry.hidden = false;
  }, 15000);
  try {
    const stationPeril = ["nearest_fire", "fireline_intensity", "satellite_fire"].includes(peril) ? "wildfire" : peril;
    const params = new URLSearchParams({peril: stationPeril, n: count, lat, lon: lng});
    const response = await fetch(`/api/v1/monitoring-stations?${params}`, {signal: controller.signal});
    if (!current()) return;
    if (response.status === 404) throw new Error("Restart the Python server to enable hazard station lookup.");
    const payload = await response.json();
    if (!current()) return;
    if (!response.ok) throw new Error(payload.error?.message || "Hazard station service unavailable.");
    const data = payload.data;
    if (!data || data.peril !== stationPeril || !["available", "not_found"].includes(data.status) || !Array.isArray(data.stations)) {
      throw new Error("Unexpected hazard station response. Restart the Python server and retry.");
    }
    const summary = `${data.network}: ${data.returned_count} of ${data.requested_count} nearest stations within ${data.search_radius_km} km of ` +
      `${selectedArea ? "the area center" : "the selected point"}, nearest first.${data.fewer_than_requested ? " Fewer eligible stations are available in this search area." : ""}`;
    renderHazardStations(data.stations, data.network, data.message, summary);
  } catch (error) {
    if (current()) {
      hazardStationStatus.textContent = `${network}: ${error.message}`;
      hazardStationRetry.hidden = false;
    }
  } finally {
    clearTimeout(timeout);
    if (pendingHazardStationRequest === controller) pendingHazardStationRequest = null;
  }
}

hazardStationShow.addEventListener("click", () => {
  if (hazardStationPoints.length && selectedPoint) map.fitBounds(L.latLngBounds([selectedPoint, ...hazardStationPoints]), {padding: [45, 45], maxZoom: 14});
});
hazardStationRetry.addEventListener("click", () => {
  if (selectedPoint) loadHazardStation(selectedPoint.lat, selectedPoint.lng, locationVersion, resolvedDisaster || disasterSelect.value);
});
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
  satellite_fire: { name: "Wildfire / Satellite fire detections", metric: "Fire intensity of the strongest satellite detection (fire radiative power)", unit: "MW", source: "NASA FIRMS", note: "Share of 2013–2025 years with a NASA satellite fire detection within the chosen radius at or above each fire radiative power. Satellite-only: crop and planned burns are included and some fires are missed." },
  nearest_fire: { name: "Wildfire / Nearby fire frequency", metric: "Reported fire size", unit: "acres", source: "USFS FPA FOD", note: "Annual frequencies count years with at least one reported fire within each radius. The nearest-fire result uses all available history. Distance is to reported incident points; property burn extent is unknown." },
  fireline_intensity: { name: "Wildfire / Fireline intensity", metric: "Fireline intensity", unit: "kW/m", source: "Texas A&M Forest Service / TxWRAP", note: "Native weather-weighted heading fireline intensity for Texas. This is modeled fire behavior, not a live reading or annual exceedance probability. Heat flux at buildings and ember exposure are separate." },
  cyclone: { name: "Wind", metric: "Maximum sustained wind (tropical cyclones)", unit: "kn", source: "NOAA NHC", note: "Tropical-cyclone wind only: NOAA's best-track wind footprints support sustained-wind historical frequencies. This is not an all-wind model; thunderstorms, tornadoes, other windstorms and local peak gusts are not modeled." },
  rainfall: { name: "Rainfall", metric: "Maximum 24-hour rainfall", unit: "mm", source: "NOAA Atlas 14", note: "Rainfall severity is accumulation depth over 24 hours, separate from cyclone wind speed." },
  heat: { name: "Heat", metric: "Annual maximum daily air temperature", unit: "°C", source: "NOAA GHCN-Daily", note: "Historical annual heat exceedance at a qualifying nearby US station, 1991–2020. Station distance and complete-year coverage are reported. This measures daily air-temperature extremes; heat-wave duration and construction WBGT require separate inputs." },
};

const triggerHelp = document.getElementById("trigger-help");
const triggerResult = document.getElementById("trigger-result");
const triggerProbability = document.getElementById("trigger-probability");
const triggerExplanation = document.getElementById("trigger-explanation");
const triggerPayout = document.getElementById("trigger-payout");
const poolContribution = document.getElementById("pool-contribution");
const poolCalculation = document.getElementById("pool-calculation");
const poolCurrency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const triggerThreshold = document.getElementById("trigger-threshold");
const triggerThresholdHelp = document.getElementById("trigger-threshold-help");
const triggerDistance = document.getElementById("trigger-distance");
const triggerDistanceHelp = document.getElementById("trigger-distance-help");
const DISTANCE_HELP = triggerDistanceHelp.textContent;
let triggerData = null;
// Source levels the typed threshold is estimated from; never listed as choices.
let triggerLevels = [];
// A typed threshold keeps its meaning only within one hazard's units.
let thresholdPeril = null;

// The active trigger: the typed threshold, as a source value or an estimate.
function activeTriggerLevel() {
  if (!triggerData || triggerThreshold.value.trim() === "") return null;
  // Reported-fire curves are observed counts: past the record they are bounded, never extrapolated.
  return hazardCustomTriggerLevel(triggerLevels, triggerThreshold.valueAsNumber, { extrapolate: !["nearest_fire", "satellite_fire"].includes(triggerData.peril) });
}

function formatTriggerProbability(level) {
  const approx = level.custom && level.method !== "source" ? (level.bound === "upper" ? "≤ " : level.bound === "lower" ? "≥ " : "≈ ") : "";
  return approx + (level.minimum !== level.maximum
    ? `${formatHazardNumber(level.minimum)}–${formatHazardNumber(level.maximum)}%`
    : `${formatHazardNumber(level.minimum)}%`);
}

function updateThresholdHelp() {
  triggerThresholdHelp.hidden = !triggerData;
  if (!triggerData) return;
  const unit = triggerData.unit || disasterTypes[triggerData.peril].unit;
  const lowest = triggerLevels[0].severity, highest = triggerLevels[triggerLevels.length - 1].severity;
  const level = activeTriggerLevel();
  triggerThreshold.setAttribute("aria-invalid", String(!!level?.error || triggerThreshold.validity.badInput));
  triggerThresholdHelp.textContent = level?.error
    ? level.error
    : level ? hazardCustomTriggerNote(level, unit)
    : `Enter a threshold in ${unit}. Source levels run from ${formatHazardNumber(lowest)} to ${formatHazardNumber(highest)} ${unit}; values between them are interpolated and values outside are ${triggerData.peril === "nearest_fire" ? "shown as bounds, not extrapolated" : "extrapolated"}.`;
}
let intensityData = null;
let historicalHeatData = null;

function renderPoolContribution() {
  poolContribution.textContent = "—";
  const empty = triggerPayout.value.trim() === "";
  const payout = triggerPayout.valueAsNumber;
  const valid = !empty && triggerPayout.validity.valid && Number.isFinite(payout) && payout >= 0;
  triggerPayout.setAttribute("aria-invalid", String(!valid && (!empty || triggerPayout.validity.badInput)));
  if (!triggerData) {
    poolCalculation.textContent = "Select a location and enter an intensity to calculate the contribution.";
    return;
  }
  const level = activeTriggerLevel();
  if (!level || level.error) {
    poolCalculation.textContent = level?.error || "Enter an intensity to calculate the contribution.";
    return;
  }
  if (!valid) {
    poolCalculation.textContent = empty && !triggerPayout.validity.badInput
      ? "Select or enter a payout amount in USD."
      : "Enter a non-negative payout in USD with no more than two decimal places.";
    return;
  }
  const contribution = hazardPoolContribution(level, payout);
  if (!contribution) {
    poolCalculation.textContent = "A valid trigger probability is required to calculate the contribution.";
    return;
  }
  poolContribution.textContent = contribution.minimum !== contribution.maximum
    ? `${poolCurrency.format(contribution.minimum)}–${poolCurrency.format(contribution.maximum)}`
    : poolCurrency.format(contribution.minimum);
  const probability = formatTriggerProbability(level);
  const estimated = level.custom && level.method !== "source";
  if (estimated) poolContribution.textContent = probability.slice(0, 2) + poolContribution.textContent;
  poolCalculation.textContent = `${probability} × ${poolCurrency.format(payout)} = ${poolContribution.textContent} per year.${triggerData.area ? " Based on the average of available grid-point probabilities." : ""}`;
  if (estimated) poolCalculation.textContent += level.method === "bounded"
    ? ` Uses ${level.bound === "upper" ? "an upper" : "a lower"}-bound frequency, not a source value or an estimate.`
    : ` Uses an ${level.method} probability, not a source value.`;
  if (["cyclone", "heat", "nearest_fire"].includes(triggerData.peril)) poolCalculation.textContent += " Uses observed annual frequency.";
  if (triggerData.peril === "nearest_fire") poolCalculation.textContent += ` Historical ${triggerData.baseline_label} reported-fire illustration; not a forward-looking price.`;
  if (triggerData.peril === "heat") poolCalculation.textContent += " Historical 1991–2020 station-proxy illustration; not a forward-looking price.";
}

function resetTrigger(message, state = "idle") {
  clearAreaSamples();
  triggerData = null;
  triggerLevels = [];
  triggerThreshold.disabled = true;
  updateThresholdHelp();
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
  const unit = data.unit || type.unit;
  document.getElementById("trigger-threshold-unit").textContent = unit;
  triggerThreshold.disabled = false;
  // Open with a probability showing: keep the user's threshold within the same hazard,
  // otherwise start from the source level with the highest probability.
  let prefilled = false;
  if (thresholdPeril !== data.peril || triggerThreshold.value.trim() === "") {
    const start = levels.reduce((best, level) => level.maximum > best.maximum ? level : best, levels[0]);
    triggerThreshold.value = String(start.severity);
    thresholdPeril = data.peril;
    prefilled = true;
  }
  const missing = data.points.filter(point => !Number.isFinite(point.severity)).length;
  triggerHelp.textContent = `${type.metric} · estimated from ${levels.length} source level${levels.length === 1 ? "" : "s"}. ${missing ? `${missing} missing source level${missing === 1 ? " is" : "s are"} omitted. ` : ""}` +
    (data.area ? "Probabilities are averaged at each severity across 25 grid samples. " : "") +
    (data.peril === "nearest_fire" ? `Trigger: a reported fire of at least the entered size within ${formatHazardNumber(data.radius_km)} km in a year. Change the distance above (up to 20 km). ` : "") +
    (prefilled ? `Started at ${formatHazardNumber(triggerThreshold.valueAsNumber)} ${unit}, the source level with the highest ${data.area ? "average " : ""}probability; enter any threshold.` : "");
  renderTriggerResult();
  // The monitor table's "Your point" row repeats this result; trigger levels can arrive after the table.
  renderStationRecords();
}

function renderTriggerResult() {
  clearAreaSamples();
  renderPoolContribution();
  if (!triggerData) return;
  updateThresholdHelp();
  const level = activeTriggerLevel();
  triggerProbability.textContent = "—";
  for (const id of ["trigger-location", "trigger-source", "trigger-method"]) document.getElementById(id).hidden = !level;
  document.getElementById("trigger-result-label").textContent = hazardTriggerBasis(triggerData.peril).label;
  if (!level || level.error) {
    for (const id of ["trigger-location", "trigger-source", "trigger-method"]) document.getElementById(id).hidden = true;
    triggerResult.dataset.state = "ready";
    triggerExplanation.textContent = level?.error || "Enter an intensity to see its exceedance probability.";
    return;
  }
  const data = triggerData, type = disasterTypes[data.peril];
  const basis = hazardTriggerBasis(data.peril);
  const range = level.minimum !== level.maximum;
  const estimated = level.custom && level.method !== "source";
  triggerResult.dataset.state = "available";
  triggerProbability.textContent = formatTriggerProbability(level);
  triggerExplanation.textContent = `${type.name}: ${data.metric || type.metric} at or above ${formatHazardNumber(level.severity)} ${data.unit || type.unit}.` +
    (!estimated ? "" : level.method === "bounded" ? ` ${level.bound === "upper" ? "Upper" : "Lower"} bound from the observed record, not an estimate.`
      : ` ${level.method[0].toUpperCase() + level.method.slice(1)} estimate.`);
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
  document.getElementById("trigger-method").textContent = (estimated ? hazardCustomTriggerNote(level, data.unit || type.unit) + " " : "") +
    basis.note + (range ? " Multiple source frequencies share this rounded level, so their range is shown; a unique probability is unavailable." : "");
  if (estimated) {
    if (data.area) document.getElementById("trigger-method").textContent += " For an area, the estimate is taken from the averaged curve; per-sample values are only shown for supplied levels.";
    return;
  }
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
    document.getElementById("trigger-method").textContent = `${data.area.method} ${data.peril === "cyclone" ? basis.note : "Modeled local chance of meeting or exceeding this severity in one year."} This is a mean of local probabilities, not the probability of an event anywhere in the area. It estimates the grid-point average rather than every raster cell.`;
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

triggerThreshold.addEventListener("input", () => {
  renderTriggerResult();
  renderStationRecords();
});
triggerPayout.addEventListener("input", renderPoolContribution);

// Section 4 monitor table, shared by Heat, Rainfall, Wind and Flood. Each row is one
// monitor's own annual maxima (NOAA temperature/rain/gust stations, USGS river gauges);
// rows are never averaged and do not change the main curve or trigger. Row count
// follows "Stations (n)", capped by the server at 10. A first row repeats the main
// result at the point, and numbered map pins match the rows.
const RECORD_KINDS = {
  // fromTrigger converts the main trigger threshold into the table's unit. Flood has
  // none: the trigger is water depth at the point, the gauges record river flow.
  heat: { kind: "heat", endpoint: "/api/heat-stations", title: "Temperature stations: hottest day of each year, 1991–2020", label: "Temperature threshold (°C)",
    fromTrigger: value => value,
    linkedHelp: "Uses the intensity above. A heat year counts only when every day has a valid reading, stricter than the other monitor tables.",
    compareNote: "from the station marked “used for main curve”",
    format: value => `${formatHazardNumber(Math.round(value * 10) / 10)} °C / ${formatHazardNumber(Math.round((value * 9 / 5 + 32) * 10) / 10)} °F`, round: value => Math.round(value) },
  rainfall: { kind: "rainfall", title: "Rain gauges: wettest day of each year, 1991–2020", label: "Daily rainfall threshold (mm)",
    fromTrigger: value => value,
    linkedHelp: "Uses the intensity above. The main result is NOAA Atlas 14's 24-hour depth; this table compares the same number of mm against calendar-day totals.",
    compareNote: "NOAA Atlas 14 24-hour rainfall model",
    format: value => `${formatHazardNumber(Math.round(value * 10) / 10)} mm / ${formatHazardNumber(Math.round(value / 25.4 * 100) / 100)} in`, round: value => Math.round(value) },
  cyclone: { kind: "wind", title: "Weather stations: strongest sustained wind during tropical storms, 2004–2025", label: "Sustained wind (m/s)",
    fromTrigger: value => value * 0.514444,
    linkedHelp: "Uses the intensity above. Like the main result, only tropical storms count: each station's fastest 2-minute sustained wind on days a storm's recorded wind area covered it.",
    compareNote: "estimated from storm wind areas; the stations below show what was measured",
    format: value => `${formatHazardNumber(Math.round(value * 1.94384 * 10) / 10)} kn / ${formatHazardNumber(Math.round(value * 10) / 10)} m/s`, round: value => Math.round(value) },
  // Flood: each NWS gauge is compared with its own official flood stage for the chosen level.
  flood: { kind: "flood", title: "River gauges (NWS): how often the river reached flood stage", label: "Flood level", levelSelect: true,
    compareNote: "water depth at your point; the gauges below show how often nearby rivers reached flood stage",
    ownHelp: "Each gauge has its own official flood stages, set by the National Weather Service. Choose a flood level; each row counts the water years in which that river reached its own stage. Levels come from the linked USGS record.",
    format: value => `${formatHazardNumber(Math.round(value * 100) / 100)} ft`, round: value => Math.round(value) },
};
// Satellite fire: each satellite is a monitor with its own MW record around the point.
RECORD_KINDS.satellite_fire = { kind: "satellite", endpoint: "/api/satellite-monitors", perSatellite: true,
  title: "Satellites: strongest fire detection of each year (each satellite separately)", label: "Fire intensity (MW)", compareLabel: "at this fire intensity", emptyPrompt: "a fire intensity above",
  fromTrigger: value => value,
  params: () => ({ radius_km: String(satelliteRadiusValue() ?? 25) }),
  linkedHelp: "Uses the fire intensity and search radius above. Each satellite counts its own years; the main curve uses Suomi NPP only.",
  compareNote: "from Suomi NPP, the first satellite below",
  format: value => `${formatHazardNumber(Math.round(value * 100) / 100)} MW`, round: value => Math.round(value) };
let recordsData = null;
let recordsConfig = null;
let recordsPeril = null;
let recordsMarkers = [];
let pendingRecords = null;
const recordsStatus = document.getElementById("records-status");
const recordsRows = document.getElementById("records-rows");
const recordsThreshold = document.getElementById("records-threshold");
recordsThreshold.addEventListener("input", renderStationRecords);
const recordsLevel = document.getElementById("records-level");
recordsLevel.addEventListener("change", renderStationRecords);
const recordsShow = document.getElementById("records-show");
recordsShow.addEventListener("click", () => {
  const points = recordsMarkers.map(marker => marker.getLatLng?.()).filter(Boolean);
  if (points.length && selectedPoint) {
    map.fitBounds(L.latLngBounds([selectedPoint, ...points]), {padding: [45, 45], maxZoom: 14});
    document.getElementById("map").scrollIntoView?.({behavior: "smooth", block: "center"});
  }
});

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// The heat endpoint predates the shared table; map its rows onto the same shape.
function normalizeHeatRecords(payload) {
  return {...payload, kind: "heat", minimum_years: payload.baseline?.minimum_complete_years ?? 20,
    year_rule: "A calendar year counts only when every day has a valid, unflagged daily maximum temperature.",
    stations: payload.stations.map(station => {
      const maxima = Array.isArray(station.annual_maxima_c) ? station.annual_maxima_c : [];
      return {...station, counted_years: station.complete_years, annual_maxima: maxima, median: median(maxima),
        highest: station.highest_annual_maximum_c ?? null,
        period: station.status === "source_unavailable" ? null : {start: payload.baseline?.start_year ?? 1991, end: payload.baseline?.end_year ?? 2020}};
    })};
}

function clearStationRecords() {
  pendingRecords?.abort();
  pendingRecords = null;
  recordsData = null;
  recordsConfig = null;
  recordsPeril = null;
  recordsMarkers.forEach(layer => map.removeLayer(layer));
  recordsMarkers = [];
  recordsShow.hidden = true;
  recordsRows.replaceChildren();
  recordsStatus.textContent = "";
  recordsThreshold.value = "";
  document.getElementById("records-frequency-heading").textContent = "Years at or above threshold";
  document.getElementById("records-notes").replaceChildren();
  document.getElementById("records-details").hidden = true;
}

async function loadStationRecords(peril, lat, lng, version) {
  clearStationRecords();
  const config = RECORD_KINDS[peril];
  if (!config) return;
  recordsConfig = config;
  recordsPeril = peril;
  document.getElementById("records-details").hidden = false;
  document.getElementById("records-summary").textContent = config.title;
  document.getElementById("records-threshold-label").textContent = config.label;
  // Heat, rainfall, wind and satellite fire follow the main intensity box; only flood keeps its own.
  document.getElementById("records-threshold-wrap").hidden = Boolean(config.fromTrigger || config.levelSelect);
  document.getElementById("records-level-wrap").hidden = !config.levelSelect;
  document.getElementById("records-threshold-help").textContent = config.fromTrigger ? config.linkedHelp
    : config.ownHelp;
  if (!validStationCount()) {
    recordsStatus.textContent = "Monitor table unavailable: Stations (n) must be an integer from 1 to 50.";
    return;
  }
  const controller = new AbortController();
  pendingRecords = controller;
  const current = () => !controller.signal.aborted && version === locationVersion && pendingRecords === controller;
  const shown = Math.min(Number(stationCount.value), 10);
  recordsStatus.textContent = config.perSatellite ? "Counting each satellite's detections…" : `Loading records for up to ${shown} nearby ${shown === 1 ? "monitor" : "monitors"}…`;
  try {
    const params = new URLSearchParams({ lat: String(lat), lon: String(lng), ...(config.perSatellite ? {} : { n: stationCount.value }), ...(config.params?.() || {}) });
    if (!config.endpoint) params.set("kind", config.kind);
    const response = await fetch(`${config.endpoint || "/api/station-records"}?${params}`, { signal: controller.signal });
    if (!current()) return;
    if (response.status === 404) throw new Error("Restart the Python server to enable monitor tables.");
    let payload = await response.json();
    if (!current()) return;
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (!Array.isArray(payload.stations) || (config.kind === "heat" ? payload.peril !== "heat" : payload.kind !== config.kind)) throw new Error("Unexpected monitor record response.");
    if (config.kind === "heat") payload = normalizeHeatRecords(payload);
    recordsData = payload;
    const first = payload.stations.find(station => station.status === "available");
    if (!config.fromTrigger && !config.levelSelect) recordsThreshold.value = first && Number.isFinite(first.median) ? String(config.round(first.median)) : "";
    const notes = document.getElementById("records-notes");
    for (const text of [payload.year_rule, ...(payload.notes || [])]) {
      const paragraph = document.createElement("p");
      paragraph.textContent = text;
      notes.append(paragraph);
    }
    for (const [index, station] of payload.stations.entries()) {
      if (!Number.isFinite(station.latitude) || !Number.isFinite(station.longitude)) continue;
      const popup = document.createElement("span");
      popup.textContent = `${index + 1}. ${station.name} (${station.id}) · ${station.distance_km.toFixed(2)} km`;
      const options = L.divIcon ? {icon: L.divIcon({className: "", html: `<span class="monitor-pin">${index + 1}</span>`, iconSize: [24, 24], iconAnchor: [12, 12]}), title: popup.textContent} : {};
      recordsMarkers.push(L.marker({lat: station.latitude, lng: station.longitude}, options).addTo(map).bindPopup(popup));
    }
    recordsShow.hidden = !recordsMarkers.length;
    renderStationRecords();
  } catch (error) {
    if (error.name !== "AbortError" && current()) recordsStatus.textContent = `Monitor table unavailable: ${error.message}`;
  } finally {
    if (pendingRecords === controller) pendingRecords = null;
  }
}

function tableCell(value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  return cell;
}

// Expandable full curve for one monitor: every observed annual maximum and the share
// of counted years at or above it (an empirical step distribution, no interpolation).
function monitorCurve(station, config) {
  const maxima = station.annual_maxima || [];
  const details = document.createElement("details");
  details.className = "monitor-curve";
  const summary = document.createElement("summary");
  summary.textContent = `Full curve (${maxima.length} ${maxima.length === 1 ? "year" : "years"})`;
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const label of ["At least", "Share of years"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    head.append(cell);
  }
  table.append(head);
  for (const value of [...new Set(maxima)].sort((a, b) => b - a)) {
    const hits = maxima.filter(item => item >= value).length;
    const row = document.createElement("tr");
    row.append(tableCell(config.format(value)), tableCell(`${formatHazardNumber(Math.round(1000 * hits / maxima.length) / 10)}% (${hits}/${maxima.length})`));
    table.append(row);
  }
  details.append(summary, table);
  return details;
}

function renderStationRecords() {
  const data = recordsData, config = recordsConfig;
  if (!data || !config) return;
  recordsRows.replaceChildren();
  const source = config.fromTrigger ? triggerThreshold : recordsThreshold;
  const threshold = config.fromTrigger ? config.fromTrigger(source.valueAsNumber) : source.valueAsNumber;
  const hasThreshold = source.value.trim() !== "" && Number.isFinite(threshold) && threshold >= 0;
  const level = config.levelSelect ? recordsLevel.value : null;
  const levelName = level ? recordsLevel.selectedOptions?.[0]?.textContent || `${level} flood stage` : "";
  document.getElementById("records-frequency-heading").textContent = level
    ? `Years reaching ${levelName.charAt(0).toLowerCase()}${levelName.slice(1)}`
    : hasThreshold
    ? `Years with maximum ≥ ${config.format(threshold)}`
    : `Years at or above threshold (enter ${config.emptyPrompt || (config.fromTrigger ? "an intensity above" : "a threshold")})`;
  const requested = data.requested_stations, listed = data.stations.length;
  recordsStatus.textContent = config.perSatellite
    ? (listed ? `${listed} satellites, each counted separately within ${formatHazardNumber(data.radius_km)} km of your point. Stations (n) does not apply. Rows are not averaged and do not change the result above.`
      : data.reason || "Satellite archives are unavailable.")
    : !listed
    ? `No monitor with at least ${data.minimum_years} years of records is within ${data.radius_km} km. This does not imply low hazard.`
    : `${listed} of ${data.stations_within_radius} ${data.stations_within_radius === 1 ? "monitor" : "monitors"} within ${data.radius_km} km, nearest first` +
      `${requested > data.station_limit ? ` (n = ${requested}; this table is capped at ${data.station_limit})` : listed < requested ? ` (n = ${requested}; no more monitors within ${data.radius_km} km)` : ""}. ` +
      "Each row is that monitor's own record; rows are not averaged and do not change the result above.";
  // Comparison row: the main result at the selected point, next to the monitors.
  const probability = document.getElementById("trigger-probability").textContent.trim();
  const compare = document.createElement("tr");
  compare.className = "compare-row";
  compare.append(tableCell(`Your point · main result (${disasterTypes[recordsPeril]?.source || "model"})`), tableCell("—"), tableCell("—"), tableCell("—"), tableCell("—"),
    tableCell(probability && probability !== "—" ? `${probability} chance per year ${config.compareLabel || "at this intensity"} · ${config.compareNote}` : `— (set ${config.emptyPrompt || "an intensity above"})`));
  recordsRows.append(compare);
  for (const [index, station] of data.stations.entries()) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const extras = [];
    if (station.period) extras.push(`${station.period.start}–${station.period.end}`);
    if (Number.isFinite(station.years_with_detection)) extras.push(`detections in ${station.years_with_detection} of ${station.counted_years} years`);
    if (station.overpass) extras.push(`passes ${station.overpass}`);
    if (level && Number.isFinite(station.flood_stages?.[level])) extras.push(`${level} flood stage ${formatHazardNumber(station.flood_stages[level])} ft`);
    if (station.datum_warning) extras.push(`⚠ NWS and USGS levels differ by up to ${formatHazardNumber(station.datum_check_ft)} ft for the same floods: part of the record may use a different reference height, so treat this row with caution`);
    for (const [label, count] of Object.entries(station.flagged_peaks || {})) extras.push(`${count} ${count === 1 ? "peak" : "peaks"} coded ${label}`);
    if (station.rejected_implausible_days) extras.push(`${station.rejected_implausible_days} implausible readings removed`);
    if (Number.isFinite(station.storm_years)) extras.push(`tropical storm in ${station.storm_years} ${station.storm_years === 1 ? "year" : "years"}`);
    if (station.storm_years_missing_readings) extras.push(`${station.storm_years_missing_readings} storm ${station.storm_years_missing_readings === 1 ? "year" : "years"} left out: no reading during the storm`);
    const used = config.kind === "heat" && historicalHeatData?.station?.id === station.id;
    name.textContent = `${index + 1}. ${station.name}${config.perSatellite ? "" : ` (${station.id})`}${used ? " — used for main curve" : ""}${extras.length ? ` · ${extras.join("; ")}` : ""}`;
    if ((station.annual_maxima || []).length) name.append(monitorCurve(station, config));
    row.append(name, tableCell(Number.isFinite(station.distance_km) ? station.distance_km.toFixed(2) : "—"), tableCell(station.counted_years ?? "—"),
      tableCell(Number.isFinite(station.median) ? config.format(station.median) : "—"),
      tableCell(Number.isFinite(station.highest) ? config.format(station.highest) : "—"),
      tableCell(level
        ? (Number.isFinite(station.flood_stages?.[level])
          ? stationFrequency(station, station.annual_maxima, true, station.flood_stages[level], data.minimum_years)
          : (station.status === "source_unavailable" ? station.reason : `No ${level} stage set for this gauge`))
        : stationFrequency(station, station.annual_maxima, hasThreshold, threshold, data.minimum_years)));
    recordsRows.append(row);
  }
}

// % of a station's counted years at or above the threshold. Records shorter than the
// app's 20-year minimum still get a %, but labelled unreliable: with few years each
// year is a large step and a 0% is likely even for events that do happen.
function stationFrequency(station, maxima, hasThreshold, threshold, minimumYears) {
  const usable = Array.isArray(maxima) ? maxima : [];
  const short = station.status === "insufficient_years";
  if ((station.status !== "available" && !short) || (short && !usable.length)) return station.reason || "Unavailable";
  if (!hasThreshold) return short ? `— (short record: ${usable.length} ${usable.length === 1 ? "year" : "years"} — unreliable)` : "—";
  const hits = usable.filter(value => value >= threshold).length;
  const text = `${formatHazardNumber(Math.round(1000 * hits / usable.length) / 10)}% (${hits}/${usable.length} years)`;
  return short ? `${text} ⚠ Short record: under ${minimumYears} years — unreliable` : text;
}

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
  document.getElementById("heat-history-details").hidden = true;
  document.getElementById("heat-history-rows").replaceChildren();
  document.getElementById("export-heat").disabled = true;
  clearStationRecords();
  renderSatelliteYears(null);
  document.getElementById("intensity-value").textContent = "—";
  document.getElementById("intensity-preview-value").textContent = "—";
  document.getElementById("intensity-description").textContent = "";
  document.getElementById("export-intensity").disabled = true;
}

function isFireHazard(peril) {
  return ["nearest_fire", "fireline_intensity", "satellite_fire"].includes(peril);
}

function configureDisasterView(peril) {
  const type = disasterTypes[peril];
  const history = peril === "nearest_fire";
  document.getElementById("nearest-fire-panel").hidden = !history;
  const satelliteFire = isFireHazard(peril);
  document.getElementById("satellite-fire-panel").hidden = !satelliteFire;
  document.getElementById("trigger-distance-control").hidden = triggerDistanceHelp.hidden = !history;
  // Enabled once nearby-fire records load, alongside the size threshold.
  triggerDistance.disabled = true;
  if (!satelliteFire) globalThis.SatelliteFire?.clear();
  document.getElementById("trigger-workspace").classList.toggle("history-mode", history);
  if (!history) {
    globalThis.FireHistory.clear();
    globalThis.FireProximity.clear();
  }
  const intensity = peril === "fireline_intensity";
  document.getElementById("heat-observation-panel").hidden = peril !== "heat";
  document.getElementById("trigger-heading").textContent = intensity ? "2. Explore fireline intensity" : "2. Set an intensity and see the chance";
  document.getElementById("hazard-selection-description").textContent = intensity
    ? "Select a Texas location to sample the native fireline-intensity model."
    : "Enter any intensity; probabilities are estimated from a government model’s levels for your selected location.";
  if (history) {
    document.getElementById("trigger-heading").textContent = "2. Set a fire size and see the chance";
    document.getElementById("hazard-selection-description").textContent = "Select a point to see annual nearby-fire frequencies within 5, 10 and 20 km, plus the nearest recorded wildfire. Triggers combine two measures: a reported fire size (acres) and a distance from the point (any value up to 20 km). Results check for updates every 5 minutes while this page is visible.";
  }
  // Satellite fire is set by fire intensity (fire radiative power, MW) rather than a generic "trigger".
  const intensityInput = peril === "satellite_fire";
  // Wording: hazard "intensity" (depth, shaking, wind speed, temperature…). Nearby fire is measured in acres, a size.
  const nearbyFire = peril === "nearest_fire";
  document.getElementById("trigger-threshold-name").textContent = intensityInput ? "Fire intensity" : nearbyFire ? "Fire size" : "Intensity";
  document.getElementById("trigger-payout-name").textContent = intensityInput ? "Payout if a fire of at least this intensity is detected (USD)"
    : nearbyFire ? "Payout if a fire of at least this size is reported (USD)" : "Payout if this intensity is reached (USD)";
  document.getElementById("payout-help").textContent = intensityInput
    ? "The fixed payout in a year when a satellite detects a fire at or above this intensity within the radius."
    : "The fixed payout in a year when the selected intensity is reached or exceeded.";
  if (intensityInput) {
    document.getElementById("trigger-heading").textContent = "2. Set a fire intensity and see the chance";
    document.getElementById("hazard-selection-description").textContent = "Select a point to count NASA satellite fire detections within the chosen radius, 2013–2025. Enter a fire intensity, measured as fire radiative power (MW); the chance is the share of years with a detection at least that intense.";
  }
  if (peril === "heat") document.getElementById("hazard-selection-description").textContent = "Select a point to load historical annual heat exceedance from a qualifying NOAA station. First lookup downloads the historical station inventory.";
  document.getElementById("intensity-preview").hidden = !intensity;
  document.getElementById("hazard-intensity").hidden = !intensity;
  document.getElementById("hazard-curve-layout").hidden = intensity;
  document.getElementById("hazard-axis-note").hidden = intensity;
  for (const id of ["trigger-level-control", "trigger-payout-control", "payout-help", "trigger-result"]) {
    document.getElementById(id).hidden = intensity;
  }
  document.getElementById("export-area").hidden = intensity || history || !selectedArea;
  resolvedDisaster = peril === "auto" ? null : peril;
  document.getElementById("flood-panel").hidden = peril !== "flood";
  // Section 5 only holds live readings (Heat: current station reports; wildfire: satellite detections).
  document.getElementById("context-section").hidden = !(peril === "heat" || isFireHazard(peril));
  hazardPanel.hidden = peril === "flood" || history;
  if (history && selectedArea) areaSummary.textContent = "Nearest recorded wildfire is measured from the selected area center.";
  document.getElementById("site-class-control").hidden = peril !== "earthquake";
  document.getElementById("satellite-radius-control").hidden = document.getElementById("satellite-radius-help").hidden = peril !== "satellite_fire";
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

// Nearby-fire triggers: "a reported fire of at least N acres within the selected radius in a year".
// Probability is the observed share of archive years (not the Poisson rate), like cyclone and heat.
globalThis.FireProximity?.onCurve?.((detail, message) => {
  if (disasterSelect.value !== "nearest_fire") return;
  if (!detail) {
    resetTrigger(message || "Nearby-fire trigger levels are unavailable.", "unavailable");
    // With no fire records at all, the distance is as unusable as the size. If records
    // loaded and only this distance lacks a curve, keep it editable to pick another.
    triggerDistance.disabled = !globalThis.FireProximity?.loaded;
    renderStationRecords();
    return;
  }
  triggerDistance.disabled = false;
  const { curve, baseline } = detail;
  const km = formatHazardNumber(curve.radius_km);
  if (triggerDistance.valueAsNumber !== curve.radius_km) triggerDistance.value = String(curve.radius_km);
  triggerDistance.setAttribute("aria-invalid", "false");
  triggerDistanceHelp.textContent = DISTANCE_HELP;
  loadTriggerLevels({
    status: "available", peril: "nearest_fire", radius_km: curve.radius_km,
    metric: `Reported fire size within ${km} km`, unit: "acres",
    source_name: detail.source_name, source_url: detail.source_url,
    baseline_label: `${baseline.start_year}–${baseline.end_year}`,
    reason: `No reported fire within ${km} km has an observed year. Zero observed years does not mean zero future risk; try a larger distance.`,
    // Never-observed sizes are excluded rather than priced at zero.
    points: curve.points.filter(point => point.empirical_years_pct > 0)
      .map(point => ({ severity: point.min_acres, frequency_pct: point.empirical_years_pct })),
  });
});
// Custom search radius, 0.5–100 km. "change" fires on Enter or leaving the box, so the
// curve is not reloaded on every keystroke. An invalid value leaves the last curve alone.
const satelliteRadius = document.getElementById("satellite-radius");
const SATELLITE_RADIUS_HELP = document.getElementById("satellite-radius-help").textContent;
function satelliteRadiusValue() {
  const value = satelliteRadius.valueAsNumber;
  return satelliteRadius.value.trim() !== "" && Number.isFinite(value) && value >= 0.5 && value <= 100 ? value : null;
}
satelliteRadius.addEventListener("change", () => {
  const valid = satelliteRadiusValue() !== null;
  satelliteRadius.setAttribute("aria-invalid", String(!valid));
  document.getElementById("satellite-radius-help").textContent = valid ? SATELLITE_RADIUS_HELP : "Enter a radius from 0.5 to 100 km.";
  if (valid && selectedPoint && resolvedDisaster === "satellite_fire") selectLocation(selectedPoint);
});
siteClassSelect.addEventListener("change", () => {
  if (selectedPoint && resolvedDisaster === "earthquake") selectLocation(selectedPoint);
});
// Second wildfire trigger measure: distance (km). The fire panel recomputes the size curve for it.
triggerDistance.addEventListener("input", () => {
  if (triggerDistance.value.trim() === "") return;
  const error = globalThis.FireProximity?.setDistance(triggerDistance.valueAsNumber);
  triggerDistance.setAttribute("aria-invalid", String(Boolean(error)));
  triggerDistanceHelp.textContent = error || DISTANCE_HELP;
});


async function loadSelectedHazard(lat, lng, version) {
  const peril = disasterSelect.value;
  let type = disasterTypes[peril];
  if (peril === "nearest_fire") {
    resetHazardView("Historical fire-proximity frequencies are shown below the map.");
    status.textContent = "Annual nearby-fire frequencies and the nearest recorded wildfire are shown below the map.";
    globalThis.FireHistory.load(map, { lat, lng }, Boolean(selectedArea));
    resetTrigger("Loading reported fire sizes for trigger levels…", "loading");
    globalThis.FireProximity.load(map, { lat, lng }, Boolean(selectedArea));
    return;
  }
  const controller = new AbortController();
  pendingHazardRequest = controller;
  resetHazardView(peril === "auto" ? "Comparing regional hazards and checking local government curves…" : `Loading ${type.source} data for the selected location…`);
  status.textContent = peril === "auto" ? "Choosing a relevant disaster for this location…" : `Loading ${type.name.toLowerCase()} model…`;
  try {
    const params = new URLSearchParams({ peril, lat: String(lat), lon: String(lng), site_class: siteClassSelect.value });
    if (peril === "satellite_fire" && !selectedArea) {
      if (satelliteRadiusValue() === null) satelliteRadius.value = "25";  // never send a radius the box doesn't show
      params.set("radius_km", String(satelliteRadiusValue()));
    }
    if (selectedArea) {
      params.set("south", selectedArea.getSouth());
      params.set("west", selectedArea.getWest());
      params.set("north", selectedArea.getNorth());
      params.set("east", selectedArea.getEast());
      status.textContent = peril === "fireline_intensity" ? "Sampling native fireline intensity at 25 grid points…" : "Calculating hazard curves at 25 grid points and averaging their probabilities…";
      if (peril === "heat") status.textContent = "Historical heat currently supports Point mode; checking selection…";
    }
    const requestUrl = `${selectedArea ? "/api/disaster-area" : "/api/disaster"}?${params}`;
    const response = await fetch(requestUrl, { signal: controller.signal });
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
      loadHazardStation(lat, lng, version, data.peril);
      if (data.peril === "heat") loadHeatStations(lat, lng, version);
      showRelevance(data.selection, data.peril);
      type = disasterTypes[data.peril];
      marker?.bindPopup(`${type.name} (automatic): ${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    }
    loadTriggerLevels(data);
    if (data.peril === "heat") {
      renderHistoricalHeat(data);
      if (data.status === "available" && data.station) {
        renderHazardStations([{...data.station, url: data.station.url || data.source_url}], "NOAA GHCN-Daily historical heat station",
          `This is the station used for the 1991–2020 heat curve (${data.baseline.complete_years} complete years). The heat screening panel shows separate current readings from nearby weather stations.`);
      } else {
        hazardStationStatus.textContent = data.reason || "No qualifying historical heat station available for this selection.";
      }
      if (data.area) areaSummary.textContent = data.reason;
    }
    if (!data.area && RECORD_KINDS[data.peril]) loadStationRecords(data.peril, lat, lng, version);
    if (data.peril === "satellite_fire") renderSatelliteYears(data);
    if (data.area && !["fireline_intensity", "heat"].includes(data.peril)) {
      document.getElementById("hazard-heading").textContent = `${type.name} · average sampled exceedance curve`;
      hazardPanel.hidden = false;
      document.getElementById("coverage-column").hidden = false;
      areaSummary.textContent = `Selected area centroid: ${data.area.centroid.latitude.toFixed(6)}, ${data.area.centroid.longitude.toFixed(6)}. ${data.area.available_samples || 0}/${data.area.sample_count} grid locations have usable curves. Each curve point averages available probabilities at the same severity. Change the trigger to inspect all sample values below. This uses a 5×5 grid inside and on the boundary, not every raster cell. Automatic hazard selection uses county context at the area centroid.`;
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
    document.getElementById("hazard-axis-note").textContent = `X: ${frequencyLabel} · Y: ${severityLabel}. ` + (data.peril === "heat" ? "Observed annual maxima only. Connecting lines guide the eye; trigger values are not interpolated or extrapolated." : "Lines guide the eye between supplied points.");
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
      if (["auto", "heat"].includes(peril)) hazardStationStatus.textContent = "Hazard station unavailable because the hazard lookup failed. Select the hazard or location again to retry.";
    }
  } finally {
    if (pendingHazardRequest === controller) pendingHazardRequest = null;
  }
}

// Year-by-year satellite record behind the satellite-fire curve: each year counts once,
// at its strongest detection within the radius. Years without detections stay visible.
function renderSatelliteYears(data) {
  const details = document.getElementById("satellite-years-details");
  const rows = document.getElementById("satellite-years-rows");
  rows.replaceChildren();
  const years = Array.isArray(data?.annual_detections) ? data.annual_detections : [];
  details.hidden = !years.length;
  if (!years.length) return;
  const active = years.filter(year => year.detections > 0).length;
  document.getElementById("satellite-years-context").textContent = `${active} of ${years.length} years had at least one presumed vegetation-fire detection within ${data.radius_km} km (Suomi NPP VIIRS only). Dates are the satellite overpass date in UTC.`;
  for (const year of years) {
    const row = document.createElement("tr");
    for (const value of [year.year, year.detections.toLocaleString(), Number.isFinite(year.max_frp_mw) ? formatHazardNumber(year.max_frp_mw) : "—", year.max_date || "—"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    rows.append(row);
  }
}

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
  if (!triggerData?.area || !activeTriggerLevel()) return;
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
  loadHazardStation(lat, lng, version, disasterSelect.value);
  loadSelectedHazard(lat, lng, version);
  if (disasterSelect.value === "heat") loadHeatStations(lat, lng, version);
  if (isFireHazard(disasterSelect.value)) globalThis.SatelliteFire?.load(map, { lat, lng }, Boolean(selectedArea));
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
  selectionHelp.textContent = disasterSelect.value === "nearest_fire"
    ? "Click two opposite corners to draw an area. Find the nearest recorded wildfire from its center. Press Escape to cancel."
    : disasterSelect.value === "fireline_intensity"
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
    hazardStationStatus.textContent = "n must be a positive integer from 1 to 50.";
    return;
  }
  clearStation();
  // Leave the current point's hazard lookup and fire-history panels alone until the address matches:
  // selectLocation() replaces them on a match, and a failed or ambiguous search must not strand them
  // half-cleared ("Loading…" with an aborted request, or fire panels waiting forever).
  clearHazardStation();
  hazardStationStatus.textContent = "Waiting for a unique address match…";
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
      hazardStationStatus.textContent = "Stations were not searched: enter a unique address or select a point on the map.";
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
      hazardStationStatus.textContent = "Stations were not searched because address lookup failed.";
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
