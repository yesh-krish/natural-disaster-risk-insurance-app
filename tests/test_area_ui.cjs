const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the complete application script with deterministic DOM, map, and API adapters.
const nodes = new Map(), events = {}, layers = new Set(), requests = [], downloads = [];
const stationRequests = [], hazardStationRequests = [], timers = new Map();
const fireLoads = [], fireClears = [];
const proximityLoads = [], proximityClears = [];
let proximityCurve = null;
function node(id = '') {
  if (id && nodes.has(id)) return nodes.get(id);
  const n = { value: '', textContent: '', hidden: false, disabled: false, children: [], dataset: {},
    validity: { valid: true }, classList: { add() {}, remove() {}, toggle() {} },
    get valueAsNumber() { return Number(this.value); },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    attributes: {}, setAttribute(name, value) { this.attributes[name] = value; },
    focus() { this.focused = true; }, addEventListener(type, fn) { this[type] = fn; },
    querySelector(selector) { return this.parts[selector] ||= node(); }, parts: {},
    remove() {}, click() { downloads.push(this); } };
  if (id) nodes.set(id, n);
  return n;
}
node('disaster-select').value = 'rainfall';
node('site-class').value = 'BC';
node('selection-mode').value = 'point';
node('station-count').value = '5';
const map = { setView() { return this; }, on(event, fn) { events[event] = fn; },
  fitBounds(bounds) { this.fitted = bounds; },
  removeLayer(layer) { layers.delete(layer); }, doubleClickZoom: { enable() {}, disable() {} } };
function layer() {
  return { addTo() { layers.add(this); return this; }, setLatLng() { return this; },
    setBounds() {}, bindPopup(text) { this.popup = text; return this; } };
}
const blobs = [];
class LocalURL extends URL {
  static createObjectURL(blob) { blobs.push(blob); return 'blob:test'; }
  static revokeObjectURL() {}
}
const context = vm.createContext({
  FireHistory: { load(...args) { fireLoads.push(args); }, clear(...args) { fireClears.push(args); } },
  FireProximity: { load(...args) { proximityLoads.push(args); }, clear(...args) { proximityClears.push(args); }, onCurve(fn) { proximityCurve = fn; } },
  console, Intl, AbortController, URLSearchParams, URL: LocalURL, Blob,
  setTimeout(fn, ms) { const id = Symbol(); timers.set(id, {fn, ms}); return id; },
  clearTimeout(id) { timers.delete(id); },
  document: { getElementById: node, createElement: () => node(), body: node(), addEventListener() {} },
  Option: function(text, value) { this.textContent = text; this.value = value; },
  L: { map: () => map, maplibreGL: layer, marker: layer, circleMarker: layer, rectangle: layer,
    latLng: (lat, lng) => ({ lat, lng }),
    latLngBounds(a, b) { if (Array.isArray(a)) return {points: a}; return { getSouth: () => Math.min(a.lat, b.lat), getNorth: () => Math.max(a.lat, b.lat),
      getWest: () => Math.min(a.lng, b.lng), getEast: () => Math.max(a.lng, b.lng),
      getCenter: () => ({ lat: (a.lat + b.lat) / 2, lng: (a.lng + b.lng) / 2 }) }; } },
  drawHazardChart() {}, formatHazardNumber: value => String(value), HAZUS_CURVES: {},
  ...require('../hazard_trigger.js'),
  fetch(url, options) { return new Promise(resolve => (url.startsWith('/api/v1/monitoring-stations?')
    ? (new URL(url, 'http://localhost').searchParams.has('peril') ? hazardStationRequests : stationRequests)
    : requests).push({ url, options, resolve })); },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../script.js'), 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const data = {
  peril: 'rainfall', status: 'available', unit: 'mm', metric: 'Rainfall', source_name: 'Test source', notes: [],
  points: [
    { severity: 10, frequency_pct: 13, sum_frequency_pct: 325,
      sample_values: Array.from({length:25}, (_, i) => ({latitude: Math.floor(i / 5), longitude: i % 5, frequency_pct: i + 1, method: i < 5 ? 'interpolated' : 'source'})),
      evaluated_samples: 25, missing_samples: 0, interpolated_samples: 5 },
    { severity: 20, frequency_pct: 9.5, sum_frequency_pct: 190,
      sample_values: Array.from({length:25}, (_, i) => ({latitude: Math.floor(i / 5), longitude: i % 5, frequency_pct: i < 20 ? i : null, method: i < 20 ? 'source' : 'unavailable'})),
      evaluated_samples: 20, missing_samples: 5, interpolated_samples: 0 },
  ],
  area: { centroid: { latitude: 2, longitude: 2 }, bounds: { south: 0, west: 0, north: 4, east: 4 },
    sample_count: 25, available_samples: 25, method: '5×5 sample grid', aggregation: 'arithmetic_mean' },
};

(async () => {
  assert.equal(node('nearest-fire-panel').hidden, true);
  assert.equal(node('satellite-fire-panel').hidden, true, 'satellite fire observations are only for wildfire hazards');
  assert.equal(fireLoads.length, 0, 'other hazards never request nearest fire');
  assert.equal(proximityLoads.length, 0, 'other hazards never request annual fire frequencies');
  node('selection-mode').value = 'area'; node('selection-mode').change();
  events.click({ latlng: { lat: 0, lng: 0 } });
  events.click({ latlng: { lat: 4, lng: 4 } });
  assert.match(requests[0].url, /^\/api\/disaster-area\?/);
  assert.match(requests[0].url, /north=4/);
  requests[0].resolve({ ok: true, json: async () => data });
  await flush();
  assert.equal(node('trigger-probability').textContent, '13%', 'area shows an initial average automatically');
  assert.match(node('area-average').textContent, /325% ÷ 25 valid samples = 13%/);
  assert.equal(node('area-sample-details').hidden, false);
  assert.equal(node('area-sample-rows').children.length, 25);
  assert.equal(node('export-area').disabled, false);
  const threshold = node('trigger-threshold');
  const setThreshold = value => { threshold.value = String(value); threshold.input(); };
  assert.equal(threshold.value, '10', 'area starts at the level with the highest average probability');
  assert.equal(threshold.disabled, false);
  assert.equal(node('pool-contribution').textContent, '—', 'probabilities are visible before a payout is entered');
  assert.match(node('trigger-method').textContent, /not the probability of an event anywhere/);
  node('trigger-payout').value = '10000'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$1,300.00');
  assert.match(node('pool-calculation').textContent, /average/);
  const firstMarkers = [...layers].filter(l => /^Sample:/.test(l.popup || ''));
  assert.equal(firstMarkers.length, 25);
  setThreshold(20);
  assert.equal(node('trigger-probability').textContent, '9.5%', 'a threshold at a source level uses the source average');
  assert.equal(node('pool-contribution').textContent, '$950.00');
  assert.match(node('area-average').textContent, /190% ÷ 20 valid samples = 9.5%/);
  assert.match(node('area-average').textContent, /Partial coverage/);
  assert.equal(node('area-sample-rows').children.length, 25);
  assert.ok(firstMarkers.every(l => !layers.has(l)), 'changing trigger removes previous sample markers');
  node('export-area').click();
  const csv = await blobs[0].text();
  assert.match(csv, /area_centroid_latitude/);
  assert.match(csv, /rainfall,20,mm,average,,,9.5,arithmetic_mean/);
  assert.match(csv, /rainfall,20,mm,sample,1,0,5,source/);
  assert.match(csv, /rainfall,20,mm,sample,0,0,0,source/, 'real zero probabilities stay numeric');
  assert.match(csv, /rainfall,20,mm,sample,4,0,,unavailable/, 'missing probabilities stay blank');
  assert.equal(csv.trim().split('\r\n').length, 53, 'all thresholds, averages, and 25 samples per threshold are exported');
  node('redraw-area').click();
  events.click({ latlng: { lat: 1, lng: 1 } }); events.click({ latlng: { lat: 3, lng: 3 } });
  const stale = requests[1];
  assert.equal(threshold.disabled, true, 'the threshold waits while the area changes');
  assert.equal(node('trigger-probability').textContent, '—', 'the old result clears immediately when the area changes');
  node('selection-mode').value = 'point'; node('selection-mode').change();
  assert.equal(stale.options.signal.aborted, true);
  stale.resolve({ ok: true, json: async () => data });
  await flush();
  assert.equal(node('area-average').hidden, true, 'stale area response cannot restore the average');
  assert.equal(node('area-sample-details').hidden, true);
  assert.equal(node('export-area').hidden, true);
  assert.match(requests[2].url, /^\/api\/disaster\?/);
  node('selection-mode').value = 'area'; node('selection-mode').change();
  events.click({ latlng: { lat: 0, lng: 0 } }); events.click({ latlng: { lat: 4, lng: 4 } });
  requests[3].resolve({ ok: false, status: 404, json: async () => { throw new Error('HTML error pages are not JSON'); } });
  await flush();
  assert.equal(threshold.disabled, true);
  assert.match(node('trigger-help').textContent, /Restart the Python server/);
  assert.doesNotMatch(node('trigger-help').textContent, /government|TWDB|coverage/);
  node('redraw-area').click();
  events.click({ latlng: { lat: 0, lng: 0 } }); events.click({ latlng: { lat: 4, lng: 4 } });
  requests[4].resolve({ ok: true, status: 200, json: async () => data });
  await flush();
  assert.equal(threshold.disabled, false, 'area triggers recover after updated backend is available');
  assert.equal(node('trigger-probability').textContent, '9.5%', 'the typed threshold is kept for the same hazard');
  setThreshold(15);
  assert.match(node('trigger-probability').textContent, /^≈ /, 'between source levels the area average is interpolated');
  assert.match(node('trigger-method').textContent, /log-probability interpolation between the supplied 10 mm and 20 mm/);
  assert.equal(node('area-sample-details').hidden, true, 'per-sample values are only shown at source levels');
  node('redraw-area').click();
  events.click({ latlng: { lat: 0, lng: 0 } }); events.click({ latlng: { lat: 4, lng: 4 } });
  requests[5].resolve({ ok: true, status: 200, json: async () => ({...data, area: {...data.area, aggregation: undefined}}) });
  await flush();
  assert.equal(threshold.disabled, true, 'an outdated maximum response must never be labeled as an average');
  assert.match(node('trigger-help').textContent, /updated backend/);
  // Only stations from the selected hazard's network are requested and shown; no METAR list.
  assert.equal(stationRequests.length, 0, 'non-heat hazards never request METAR weather stations');
  const listedStations = () => node('hazard-station-details').children[0]?.children || [];
  const multiStation = (peril, network, stations, extra = {}) => ({data: {peril, network, status: stations.length ? 'available' : 'not_found',
    station: stations[0] || null, stations, requested_count: 5, returned_count: stations.length, search_radius_km: 200,
    fewer_than_requested: stations.length < 5, message: 'Station context only, within 200 km.', ...extra}});
  const gauges = [
    {id: 'G1', name: 'First gauge', latitude: 2.1, longitude: 2.1, distance_km: 1.23, url: 'https://waterdata.usgs.gov/monitoring-location/USGS-G1/'},
    {id: 'G2', name: '<script>Gauge</script>', latitude: 2.2, longitude: 2.2, distance_km: 2.34, url: 'https://evil.example/x'},
  ];
  let stationParams = new URL(hazardStationRequests.at(-1).url, 'http://localhost').searchParams;
  assert.equal(stationParams.get('peril'), 'rainfall');
  assert.equal(stationParams.get('n'), '5', 'Stations (n) sets how many hazard stations are requested');
  hazardStationRequests.at(-1).resolve({ok: true, json: async () => multiStation('rainfall', 'USGS precipitation gauges', gauges)});
  await flush();
  assert.match(node('hazard-station-status').textContent, /USGS precipitation gauges: 2 of 5.*area center.*Fewer eligible/);
  assert.equal(listedStations().length, 2);
  assert.equal(listedStations()[0].children[0].textContent, 'First gauge (G1) · 1.23 km');
  assert.equal(listedStations()[0].children[2].href, 'https://waterdata.usgs.gov/monitoring-location/USGS-G1/');
  assert.equal(listedStations()[1].children[0].textContent, '<script>Gauge</script> (G2) · 2.34 km', 'provider text stays inert');
  assert.equal(listedStations()[1].children.length, 2, 'unsafe provider URL is omitted');
  const firstStationLayer = [...layers].find(l => l.popup?.textContent === 'USGS precipitation gauges: 1. First gauge (G1) · 1.23 km');
  assert.ok(firstStationLayer, 'every hazard station gets a map marker');
  assert.ok([...layers].some(l => l.popup?.textContent?.startsWith('USGS precipitation gauges: 2. <script>Gauge')));
  node('hazard-station-show').click();
  assert.equal(map.fitted.points.length, 3, 'fit includes selection and every station');
  const staleStationRequest = hazardStationRequests.at(-1);
  node('redraw-area').click();
  events.click({ latlng: { lat: 1, lng: 1 } }); events.click({ latlng: { lat: 3, lng: 3 } });
  assert.ok(!layers.has(firstStationLayer), 'new location removes old station markers immediately');
  assert.equal(listedStations().length, 0);
  staleStationRequest.resolve({ok: true, json: async () => multiStation('rainfall', 'Old', gauges)});
  await flush();
  assert.match(node('hazard-station-status').textContent, /Finding the nearest 5 stations/, 'stale response cannot overwrite current selection');
  hazardStationRequests.at(-1).resolve({ok: false, status: 502, json: async () => ({error: {message: 'Station service unavailable'}})});
  await flush();
  assert.match(node('hazard-station-status').textContent, /Station service unavailable/);
  assert.equal(node('hazard-station-retry').hidden, false);
  node('station-count').value = '3'; node('station-count').input();
  const countRequest = hazardStationRequests.at(-1);
  assert.equal(new URL(countRequest.url, 'http://localhost').searchParams.get('n'), '3');
  assert.match(node('hazard-station-status').textContent, /nearest 3 stations/);
  const beforeInvalid = hazardStationRequests.length;
  for (const invalid of ['', '0', '-1', '1.5', '1e1', '51']) {
    node('station-count').value = invalid; node('station-count').input();
    assert.match(node('hazard-station-status').textContent, /positive integer/);
  }
  assert.equal(hazardStationRequests.length, beforeInvalid, 'invalid counts never call the backend');
  assert.equal(countRequest.options.signal.aborted, true);
  node('station-count').value = '1'; node('station-count').input();
  assert.match(node('hazard-station-status').textContent, /nearest 1 station in/);
  node('station-count').value = '5'; node('station-count').input();
  hazardStationRequests.at(-1).resolve({ok: true, json: async () => multiStation('rainfall', 'USGS precipitation gauges', [])});
  await flush();
  assert.match(node('hazard-station-status').textContent, /No eligible station/);
  assert.equal(node('hazard-station-show').hidden, true);
  assert.equal(stationRequests.length, 0, 'still no METAR requests outside Heat');
  // Point comparisons retain historical zeros and duplicate-level ranges,
  // without reusing the prior area's sample counts or calculating missing data.
  node('selection-mode').value = 'point'; node('selection-mode').change();
  node('disaster-select').value = 'cyclone'; node('disaster-select').change();
  requests.at(-1).resolve({ok: true, json: async () => ({
    peril: 'cyclone', status: 'available', unit: 'kn', source_name: 'Historical wind source',
    points: [{severity: 34, frequency_pct: 10}, {severity: 34, frequency_pct: 2},
      {severity: 64, frequency_pct: 0}, {severity: null, frequency_pct: 20}],
  })});
  await flush();
  assert.match(node('trigger-help').textContent, /estimated from 2 source levels/, 'duplicate levels merge and missing severity is omitted');
  assert.equal(threshold.value, '34', 'a new hazard starts at its most frequent level');
  assert.equal(node('trigger-probability').textContent, '2–10%', 'the probability shows without typing');
  assert.match(node('trigger-help').textContent, /Started at 34 kn, the source level with the highest probability; enter any threshold/);
  assert.doesNotMatch(node('trigger-help').textContent, /average/, 'point wording does not claim an area average');
  assert.match(node('trigger-method').textContent, /Zero observed years does not mean zero future risk/);
  assert.match(node('trigger-method').textContent, /range is shown/);
  node('trigger-payout').value = '10000'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$200.00–$1,000.00');
  node('trigger-payout').value = '25000'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$500.00–$2,500.00');
  for (const payout of ['', '-1', 'not a number']) {
    node('trigger-payout').value = payout; node('trigger-payout').input();
    assert.equal(node('pool-contribution').textContent, '—', 'invalid payouts clear the contribution');
    assert.equal(node('trigger-probability').textContent, '2–10%', 'invalid payouts preserve source frequencies');
  }
  node('trigger-payout').value = '100.001'; node('trigger-payout').validity.valid = false;
  node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '—', 'fractional-cent payouts do not calculate');
  node('trigger-payout').validity.valid = true;
  node('trigger-payout').value = '0'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$0.00', 'zero is a valid payout');
  setThreshold(64);
  assert.equal(node('trigger-probability').textContent, '0%', 'a historical zero at a source level stays zero');
  setThreshold(80);
  assert.equal(node('trigger-probability').textContent, '≤ 0%', 'above a zero level only an upper bound is shown');
  assert.match(node('trigger-threshold-help').textContent, /does not establish zero future risk/);
  setThreshold(-5);
  assert.equal(node('trigger-probability').textContent, '—');
  assert.match(node('trigger-explanation').textContent, /non-negative/);
  setThreshold('');
  assert.match(node('trigger-explanation').textContent, /Enter an intensity/);
  node('disaster-select').value = 'earthquake'; node('disaster-select').change();
  assert.equal(threshold.disabled, true);
  requests.at(-1).resolve({ok: true, json: async () => ({
    peril: 'earthquake', status: 'available', unit: '%g', points: [{severity: 12, frequency_pct: 1}],
  })});
  await flush();
  assert.equal(threshold.value, '12', 'the cyclone threshold is not reused in earthquake units');
  assert.equal(node('trigger-probability').textContent, '1%', 'a single available level is used automatically');
  assert.match(node('trigger-location').textContent, /Site class BC/);
  setThreshold(20);
  assert.match(node('trigger-explanation').textContent, /no slope to extrapolate/, 'one level cannot be extrapolated');
  node('site-class').value = 'D'; node('site-class').change();
  assert.equal(node('trigger-probability').textContent, '—', 'site-class changes clear stale results');
  requests.at(-1).resolve({ok: true, json: async () => ({
    peril: 'earthquake', status: 'unavailable', points: [], reason: 'No supported data here.',
  })});
  await flush();
  assert.equal(threshold.disabled, true);
  assert.match(node('trigger-help').textContent, /No supported data here/);
  // Native scenario intensity must never inherit a probability, payout or curve.
  const intensity = {
    peril: 'fireline_intensity', status: 'available', unit: 'kW/m', metric: 'Fireline intensity',
    latitude: 29.62, longitude: -98.6, hazard_kind: 'scenario_intensity',
    source_name: 'Texas A&M Forest Service / TxWRAP', source_url: 'https://example.org/native-raster',
    source_references: [{title: 'Native intensity methodology', url: 'https://example.org/methods'}],
    annual_probability_available: false, trigger_unavailable_reason: 'No annual probabilities in this source.',
    hazard_value: {value: 71.296150208, unit: 'kW/m', statistic: 'weather_weighted_mean_heading_fireline_intensity'},
    points: [], notes: ['Modeled intensity, not an annual probability.'],
    provenance: {retrieved_at: '2026-10-29T12:00:00Z', sample_sha256: 'sample-hash', metadata_sha256: 'metadata-hash'},
  };
  node('disaster-select').value = 'fireline_intensity'; node('disaster-select').change();
  assert.equal(node('trigger-result').hidden, true);
  assert.equal(node('trigger-level-control').hidden, true);
  assert.equal(node('hazard-curve-layout').hidden, true);
  assert.equal(node('hazard-intensity').hidden, false);
  assert.equal(node('intensity-value').textContent, '—', 'previous hazard clears immediately');
  requests.at(-1).resolve({ok: true, json: async () => intensity});
  await flush();
  assert.match(node('intensity-value').textContent, /71.296150208 kW\/m/);
  assert.equal(node('intensity-preview-value').textContent, node('intensity-value').textContent);
  assert.equal(threshold.disabled, true);
  assert.equal(node('trigger-probability').textContent, '—');
  assert.equal(node('pool-contribution').textContent, '—');
  assert.match(node('hazard-status').textContent, /Annual exceedance probabilities are not supplied/);
  assert.equal(node('export-intensity').disabled, false);
  node('export-intensity').click();
  let exported = await blobs.at(-1).text();
  assert.match(exported, /intensity_kw_per_m/);
  assert.match(exported, /71.296150208/);
  assert.match(exported, /sample-hash,metadata-hash,false/);
  assert.doesNotMatch(exported, /exceedance_pct|contribution/);

  events.click({latlng: {lat: 40.7, lng: -74}});
  assert.equal(node('intensity-value').textContent, '—', 'location change clears native value');
  assert.equal(node('export-intensity').disabled, true);
  requests.at(-1).resolve({ok: true, json: async () => ({...intensity, status: 'unavailable',
    hazard_value: {...intensity.hazard_value, value: null}, reason: 'Outside Texas coverage.'})});
  await flush();
  assert.equal(node('intensity-value').textContent, 'Unavailable');
  assert.match(node('hazard-status').textContent, /Outside Texas coverage/);
  assert.equal(node('export-intensity').disabled, true);

  node('selection-mode').value = 'area'; node('selection-mode').change();
  events.click({latlng: {lat: 0, lng: 0}}); events.click({latlng: {lat: 4, lng: 4}});
  const scalarArea = {...intensity, hazard_value: {...intensity.hazard_value, value: 10},
    area: {bounds: {south: 0, west: 0, north: 4, east: 4}, centroid: {latitude: 2, longitude: 2},
      sample_count: 25, available_samples: 20, aggregation: 'arithmetic_mean', method: 'Mean native intensities.',
      summary: {minimum: 0, maximum: 20, sum: 200, missing_samples: 5},
      samples: Array.from({length: 25}, (_, i) => ({latitude: Math.floor(i / 5), longitude: i % 5,
        value: i < 5 ? null : 10, provenance: intensity.provenance}))}};
  requests.at(-1).resolve({ok: true, json: async () => scalarArea});
  await flush();
  assert.equal(node('intensity-value').textContent, '10 kW/m');
  assert.equal(node('area-sample-value').textContent, 'Fireline intensity (kW/m)');
  assert.equal(node('area-sample-rows').children.length, 25);
  assert.match(node('area-summary').textContent, /20\/25.*200 kW\/m ÷ 20 = 10 kW\/m/);
  assert.equal(node('export-area').hidden, true);
  node('export-intensity').click();
  exported = await blobs.at(-1).text();
  assert.equal(exported.trim().split('\r\n').length, 27, 'header, spatial mean and all 25 samples');
  assert.match(exported, /fireline_intensity,sample,0,0,,/);
  node('disaster-select').value = 'rainfall'; node('disaster-select').change();
  assert.equal(node('hazard-intensity').hidden, true);
  assert.equal(node('intensity-preview').hidden, true);
  assert.equal(node('hazard-curve-layout').hidden, false);
  assert.equal(node('trigger-result').hidden, false);
  assert.equal(node('trigger-level-control').hidden, false);
  assert.equal(node('intensity-value').textContent, '—');
  assert.equal(node('export-intensity').disabled, true);
  assert.equal(node('area-sample-rows').children.length, 0);
  // Address lookup must resolve uniquely before the station request is sent.
  node('address-input').value = '100 Dolorosa St, San Antonio, TX';
  let addressSearch = node('address-search').submit({preventDefault() {}});
  assert.match(requests.at(-1).url, /^\/api\/geocode\?/);
  assert.equal(listedStations().length, 0);
  requests.at(-1).resolve({ok: true, json: async () => ({status: 'matched',
    matched_address: '100 DOLOROSA, SAN ANTONIO, TX, 78205', latitude: 29.424206, longitude: -98.493261})});
  await addressSearch;
  assert.match(node('search-status').textContent, /Matched: 100 DOLOROSA/);
  stationParams = new URL(hazardStationRequests.at(-1).url, 'http://localhost').searchParams;
  assert.equal(stationParams.get('lat'), '29.424206');
  assert.equal(stationParams.get('lon'), '-98.493261');
  assert.equal(stationParams.get('n'), '5');
  const stationCountBeforeAmbiguity = hazardStationRequests.length;
  for (const status of ['ambiguous', 'no_match']) {
    addressSearch = node('address-search').submit({preventDefault() {}});
    requests.at(-1).resolve({ok: true, json: async () => ({status, message: 'Refine the address.'})});
    await addressSearch;
    assert.match(node('hazard-station-status').textContent, /Stations were not searched/);
    assert.equal(hazardStationRequests.length, stationCountBeforeAmbiguity);
    assert.equal(listedStations().length, 0);
  }
  node('disaster-select').value = 'cyclone'; node('disaster-select').change();
  assert.match(requests.at(-1).url, /peril=cyclone/, 'Wind keeps its existing API identifier');
  assert.equal(node('heat-list').children.length, 0, 'hazard changes clear the previous heat snapshot');
  assert.equal(node('heat-observation-panel').hidden, true, 'latest heat context is hidden for other hazards');
  node('disaster-select').value = 'heat'; node('disaster-select').change();
  assert.match(requests.at(-1).url, /^\/api\/disaster\?.*peril=heat/);
  assert.equal(node('heat-observation-panel').hidden, false);
  // Heat is the one hazard whose relevant current readings come from METAR weather stations.
  const heatStationParams = new URL(stationRequests.at(-1).url, 'http://localhost').searchParams;
  assert.equal(heatStationParams.get('network'), 'awc_metar');
  assert.equal(heatStationParams.get('n'), '5');
  assert.equal(heatStationParams.has('peril'), false);
  const stationData = {data: { status: 'available', network: 'NOAA AWC METAR', message: 'Network context',
    requested_count: 5, returned_count: 2, search_radius_km: 200, fewer_than_requested: true,
    coverage: 'METAR stations within 200 km', catalog_fetched_at: '2026-11-03T00:00:00Z',
    stations: [
      { id: 'KAAA', name: 'First airport', latitude: 2.1, longitude: 2.1, distance_km: 1.23,
        observation: {status: 'stale', observed_at: '2026-11-02T20:00:00Z', age_minutes: 180, values: [], message: 'Older than two hours.',
          heat: {status: 'unavailable', air_temperature_c: 0, heat_index_f: null, category: null, reason: 'Report is older than 2 hours; Heat Index withheld.'}}},
      { id: 'KBBB', name: '<script>Airport</script>', latitude: 2.2, longitude: 2.2, distance_km: 2.34,
        observation: {status: 'available', observed_at: '2026-11-02T22:59:00Z', age_minutes: 1, values: [], message: 'Latest report.',
          heat: {status: 'available', air_temperature_c: 37.7777778, relative_humidity_pct: 55,
            heat_index_c: 51.1, heat_index_f: 124, category: 'Danger', reason: 'Shade screening only; not WBGT.'}}},
    ] }};
  const heatRequest = stationRequests.at(-1);
  stationRequests.at(-1).resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('heat-status').textContent, /2 nearby stations.*No area average/);
  assert.equal(node('heat-list').children.length, 2);
  assert.match(node('heat-list').children[0].children[1].textContent, /0.0 °C.*32.0 °F.*UTC.*180 minutes.*Heat Index unavailable.*older than 2 hours/);
  assert.match(node('heat-list').children[1].children[1].textContent, /37.8 °C.*100.0 °F.*55.0%.*51.1 °C.*124.0 °F.*Danger.*not WBGT/);
  assert.equal(node('heat-list').children[1].children[0].textContent, '<script>Airport</script> (KBBB) · 2.34 km');
  node('heat-refresh').click();
  assert.match(node('heat-status').textContent, /Loading/);
  assert.equal(node('heat-list').children.length, 0);
  heatRequest.resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('heat-status').textContent, /Loading/, 'stale heat response is ignored');
  stationRequests.at(-1).resolve({ok: false, status: 502, json: async () => ({error: {message: 'down'}})});
  await flush();
  assert.match(node('heat-status').textContent, /unavailable.*failed/);
  node('heat-refresh').click();
  const stalledHeat = stationRequests.at(-1);
  [...timers.values()].filter(t => t.ms === 15000).at(-1).fn();
  assert.equal(stalledHeat.options.signal.aborted, true);
  assert.match(node('heat-status').textContent, /timed out/);
  node('station-count').value = '2'; node('station-count').input();
  assert.equal(new URL(stationRequests.at(-1).url, 'http://localhost').searchParams.get('n'), '2', 'Heat count changes refresh the readings');
  node('station-count').value = '5';
  const legacyStationData = JSON.parse(JSON.stringify(stationData));
  for (const station of legacyStationData.data.stations) delete station.observation.heat;
  stationRequests.at(-1).resolve({ok: true, json: async () => legacyStationData});
  await flush();
  assert.match(node('heat-list').children[0].children[1].textContent, /Heat Index unavailable.*restart the Python server/);
  node('heat-refresh').click();
  stationRequests.at(-1).resolve({ok: true, json: async () => ({data: {...stationData.data, status: 'not_found', stations: [], returned_count: 0}})});
  await flush();
  assert.match(node('heat-status').textContent, /No eligible.*does not imply low heat hazard/);
  const historicalHeat = {peril: 'heat', status: 'available', metric: 'Annual maximum daily air temperature', unit: '°C',
    source_name: 'NOAA GHCN-Daily', source_url: 'https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/USW00012921.dly',
    frequency_label: 'Observed annual exceedance frequency, 1991–2020 (%)', frequency_basis: 'historical_annual_frequency', notes: ['Historical station proxy.'],
    points: [{severity: 35, frequency_pct: 100, hit_years: 30, exposure_years: 30}, {severity: 40, frequency_pct: 100/3, hit_years: 10, exposure_years: 30}],
    station: {id: 'USW00012921', name: 'San Antonio Intl AP', latitude: 29.54, longitude: -98.48, distance_km: 13.4},
    station_selection: {checked: [{id: 'NEARER', complete_years: 0, eligible: false}, {id: 'USW00012921', complete_years: 30, eligible: true}]},
    baseline: {start_year: 1991, end_year: 2020, complete_years: 30},
    annual_maxima: Array.from({length: 30}, (_, i) => ({year: 1991+i, valid_days: 365, expected_days: 365, complete: true, maximum_c: i % 3 ? 35 : 40, maximum_date: `${1991+i}-07-01`})),
    provenance: {sources: [{sha256: 'a'.repeat(64), fetched_at: '2026-11-04T00:00:00Z'}]}};
  requests.at(-1).resolve({ok: true, json: async () => historicalHeat});
  await flush();
  assert.match(node('hazard-heading').textContent, /Heat hazard curve/);
  assert.equal(node('heat-history-rows').children.length, 30);
  assert.match(node('heat-history-context').textContent, /13.40 km.*30\/30.*NEARER: 0 complete years \(excluded\)/);
  assert.match(node('frequency-column').textContent, /Observed annual.*1991–2020/);
  assert.equal(threshold.disabled, false);
  assert.match(node('trigger-help').textContent, /estimated from 2 source levels/);
  setThreshold(40);
  node('trigger-payout').value = '30000'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$10,000.00');
  assert.match(node('pool-calculation').textContent, /observed annual frequency.*Historical 1991–2020 station-proxy/);
  // Custom threshold between the 35 °C and 40 °C levels is interpolated and labeled.
  setThreshold(37.5);
  assert.match(node('trigger-probability').textContent, /^≈ /);
  assert.match(node('trigger-method').textContent, /log-probability interpolation/);
  assert.match(node('pool-calculation').textContent, /interpolated probability, not a source value/);
  setThreshold(40);
  assert.match(node('trigger-result-label').textContent, /Observed annual heat exceedance frequency/);
  assert.match(node('trigger-location').textContent, /USW00012921.*13.40 km.*30 included years/);
  assert.match(node('hazard-station-status').textContent, /GHCN-Daily historical heat/);
  assert.match(listedStations()[0].children[0].textContent, /USW00012921.*13.40 km/);
  assert.equal(listedStations().length, 1, 'heat shows the one station its curve actually uses');
  assert.match(node('trigger-method').textContent, /not a calibrated future probability/);
  node('export-heat').click();
  const heatCsv = await blobs.at(-1).text();
  assert.equal(heatCsv.trim().split('\r\n').length, 33, 'header, two thresholds and 30 annual maxima');
  assert.match(heatCsv, /annual_maximum,.*1991,365,365,40,104,1991-07-01,true,USW00012921/);
  assert.match(heatCsv, new RegExp('a'.repeat(64)));
  // Per-station table: one row per nearby station, never averaged; row count follows n (capped at 10).
  const compareRequest = requests.find(request => request.url.startsWith('/api/heat-stations?'));
  assert.ok(compareRequest, 'point heat requests the per-station table');
  assert.equal(new URL(compareRequest.url, 'http://localhost').searchParams.get('n'), '5');
  assert.equal(node('records-details').hidden, false, 'heat uses the shared monitor table');
  assert.equal(node('records-threshold-wrap').hidden, true, 'heat follows the main trigger threshold');
  const compareData = n => ({peril: 'heat', requested_stations: n, station_limit: 10, stations_within_radius: 3, radius_km: 100,
    baseline: {start_year: 1991, end_year: 2020, minimum_complete_years: 20}, notes: ['Stations are not averaged.'], stations: [
    {id: 'NEARER', name: 'Nearer', latitude: 29.4, longitude: -98.5, distance_km: 5, status: 'insufficient_years', complete_years: 0, annual_maxima_c: [], points: [], reason: '0 complete years; at least 20 are required.'},
    {id: 'USW00012921', name: 'San Antonio Intl AP', latitude: 29.54, longitude: -98.48, distance_km: 13.4, status: 'available', complete_years: 30, highest_annual_maximum_c: 40, annual_maxima_c: historicalHeat.annual_maxima.map(year => year.maximum_c)},
    {id: 'OTHER', name: 'Other', latitude: 29.9, longitude: -98.9, distance_km: 50, status: 'available', complete_years: 20, highest_annual_maximum_c: 41, annual_maxima_c: Array.from({length: 20}, (_, i) => i < 5 ? 41 : 36)}]});
  compareRequest.resolve({ok: true, json: async () => compareData(5)});
  await flush();
  const compareRows = () => node('records-rows').children;
  assert.equal(compareRows().length, 4, 'comparison row plus three stations');
  assert.match(compareRows()[0].children[0].textContent, /Your point · main result \(NOAA GHCN-Daily\)/);
  assert.match(compareRows()[0].children[5].textContent, /chance per year at this intensity · from the station marked/);
  assert.match(node('records-status').textContent, /3 of 3 .*nearest first.*not averaged/);
  assert.match(compareRows()[2].children[0].textContent, /^2\. San Antonio Intl AP \(USW00012921\) — used for main curve · 1991–2020/);
  assert.doesNotMatch(compareRows()[3].children[0].textContent, /main curve/);
  assert.match(compareRows()[2].children[0].children[0].children[0].textContent, /Full curve \(30 years\)/, 'each station row expands to its full curve');
  assert.equal(compareRows()[1].children[0].children.length, 0, 'no curve without usable years');
  assert.equal([...layers].filter(l => /^\d+\. /.test(l.popup?.textContent || '')).length, 3, 'one numbered pin per station');
  assert.equal(node('records-show').hidden, false, 'a button zooms the map to the monitors');
  assert.match(node('records-frequency-heading').textContent, /≥ 40 °C/);
  assert.match(compareRows()[1].children[5].textContent, /20 are required/);
  assert.match(compareRows()[2].children[5].textContent, /\(10\/30 years\)/);
  assert.match(compareRows()[3].children[5].textContent, /^25% \(5\/20 years\)/);
  setThreshold(35);
  assert.match(compareRows()[3].children[5].textContent, /^100% \(20\/20 years\)/, 'threshold changes recompute each station');
  setThreshold(40);
  node('station-count').value = '12'; node('station-count').input();
  const cappedRequest = requests.at(-1);
  assert.match(cappedRequest.url, /^\/api\/heat-stations\?.*n=12/, 'changing n reloads the per-station table');
  assert.equal([...layers].filter(l => /^\d+\. /.test(l.popup?.textContent || '')).length, 0, 'reloading clears the old pins');
  cappedRequest.resolve({ok: true, json: async () => compareData(12)});
  await flush();
  assert.match(node('records-status').textContent, /n = 12; this table is capped at 10/);
  node('station-count').value = '5';
  node('selection-mode').value = 'area'; node('selection-mode').change();
  events.click({latlng: {lat: 29, lng: -99}}); events.click({latlng: {lat: 30, lng: -98}});
  const unsupportedHeatArea = {peril: 'heat', status: 'unavailable', points: [], reason: 'Historical heat supports Point mode; area curves unavailable.',
    area: {aggregation: 'arithmetic_mean', sample_count: 25, available_samples: 0, samples: [], centroid: {latitude: 29.5, longitude: -98.5}}};
  requests.at(-1).resolve({ok: true, json: async () => unsupportedHeatArea});
  await flush();
  assert.match(node('hazard-status').textContent, /Point mode/);
  assert.equal(node('hazard-station-show').hidden, true, 'unsupported heat area clears historical station marker');
  assert.match(node('hazard-station-status').textContent, /Point mode/);
  assert.equal(threshold.disabled, true);
  assert.equal(node('pool-contribution').textContent, '—');
  assert.equal(node('heat-history-details').hidden, true);
  assert.equal(node('records-details').hidden, true, 'area mode never shows the per-station table');
  assert.equal(node('export-heat').disabled, true);
  node('disaster-select').value = 'rainfall'; node('disaster-select').change();
  assert.equal(node('heat-observation-panel').hidden, true);
  assert.equal(node('heat-history-rows').children.length, 0);
  node('selection-mode').value = 'point'; node('selection-mode').change();
  // Potential intensity and flame length were removed from the map (2026-11-10).
  events.click({latlng: {lat: 30.1, lng: -97.3}});
  assert.match(requests.at(-1).url, /^\/api\/disaster\?.*peril=rainfall/);
  assert.equal(requests.filter(request => request.url.startsWith('/api/potential-fire?')).length, 0,
    'the map never requests the removed potential-intensity model');
  requests.at(-1).resolve({ok: true, json: async () => ({peril: 'rainfall', status: 'available', unit: 'mm',
    points: [{severity: 10, frequency_pct: 5}]})}); await flush();
  // Rainfall monitor table: each gauge's own wettest-day record, threshold of its own.
  const recordsRequest = requests.at(-1);
  assert.match(recordsRequest.url, /^\/api\/station-records\?(?=.*kind=rainfall)(?=.*n=5)/, 'point rainfall requests the per-gauge table');
  assert.equal(node('records-details').hidden, false);
  recordsRequest.resolve({ok: true, json: async () => ({kind: 'rainfall', requested_stations: 5, station_limit: 10, stations_within_radius: 3,
    radius_km: 100, minimum_years: 20, year_rule: 'A calendar year counts when at least 95% of its days have a valid, unflagged reading.', notes: ['Rows are not averaged.'],
    stations: [
      {id: 'USC1', name: 'Short gauge', distance_km: 3, status: 'insufficient_years', counted_years: 12, annual_maxima: Array.from({length: 12}, (_, i) => i < 3 ? 150 : 40), points: [], median: 40, highest: 150, reason: '12 counted years; at least 20 are required.'},
      {id: 'USC0', name: 'Empty gauge', distance_km: 4, status: 'insufficient_years', counted_years: 0, annual_maxima: [], points: [], median: null, highest: null, reason: '0 counted years; at least 20 are required.'},
      {id: 'USW1', name: 'Airport', distance_km: 9.5, status: 'available', counted_years: 20, period: {start: 1991, end: 2020},
        annual_maxima: Array.from({length: 20}, (_, i) => i < 4 ? 150 : 60), median: 60, highest: 150}]})});
  await flush();
  const recordRows = () => node('records-rows').children;
  assert.equal(recordRows().length, 4, 'comparison row plus three gauges');
  assert.equal(node('records-threshold-wrap').hidden, true, 'rainfall has no second threshold box');
  assert.match(node('records-threshold-help').textContent, /Uses the intensity above/);
  assert.equal(threshold.value, '10');
  assert.match(node('records-frequency-heading').textContent, /≥ 10 mm/, 'the table uses the main trigger threshold');
  assert.match(node('records-status').textContent, /3 of 3 monitors.*not averaged/);
  assert.match(recordRows()[1].children[5].textContent, /^100% \(12\/12 years\) ⚠ Short record: under 20 years — unreliable$/, 'short records show a labelled %');
  assert.match(recordRows()[2].children[5].textContent, /20 are required/, 'zero usable years shows the reason');
  assert.match(recordRows()[3].children[0].textContent, /Airport \(USW1\) · 1991–2020/);
  assert.match(recordRows()[3].children[5].textContent, /^100% \(20\/20 years\)$/, 'full records carry no warning');
  setThreshold(100);
  assert.match(recordRows()[3].children[5].textContent, /^20% \(4\/20 years\)/, 'changing the main trigger recomputes each gauge');
  assert.match(recordRows()[1].children[5].textContent, /^25% \(3\/12 years\) ⚠/);
  assert.match(node('records-frequency-heading').textContent, /≥ 100 mm/);
  assert.equal(node('records-notes').children.length, 2);
  node('station-count').value = '3'; node('station-count').input();
  assert.match(requests.at(-1).url, /^\/api\/station-records\?(?=.*kind=rainfall)(?=.*n=3)/, 'changing n reloads the monitor table');
  node('station-count').value = '5';
  const requestsBeforeHistory = requests.length;
  node('disaster-select').value = 'nearest_fire'; node('disaster-select').change();
  assert.equal(node('nearest-fire-panel').hidden, false);
  assert.equal(node('satellite-fire-panel').hidden, false, 'wildfire views show satellite fire observations');
  assert.equal(node('hazard-panel').hidden, true);
  for (const id of ['trigger-level-control', 'trigger-payout-control', 'trigger-result', 'trigger-help']) {
    assert.equal(node(id).hidden, false, `${id} is shown for nearby-fire triggers`);
  }
  assert.equal(threshold.disabled, true, 'fire triggers wait for the archive curve');
  assert.equal(node('trigger-distance').disabled, true, 'distance waits for the archive too');
  const fireCurve = radius => ({curve: {radius_km: radius, points: [
      {min_acres: 1, empirical_years_pct: 45.5, annual_probability_pct: 99.9},
      {min_acres: 10, empirical_years_pct: radius === 5 ? 6.1 : 21.2, annual_probability_pct: 30},
      {min_acres: 100, empirical_years_pct: radius === 5 ? 0 : 3.0, annual_probability_pct: 3},
      {min_acres: 300, empirical_years_pct: 0, annual_probability_pct: 0}]},
    baseline: {start_year: 1992, end_year: 2024}, source_name: 'USFS FPA FOD', source_url: 'https://apps.fs.usda.gov/fpa'});
  proximityCurve(fireCurve(10));
  assert.equal(threshold.disabled, false);
  assert.equal(node('trigger-distance').disabled, false, 'distance is usable once records load');
  assert.match(node('trigger-help').textContent, /estimated from 3 source levels/, 'never-observed sizes are not used as zero-probability levels');
  assert.match(node('trigger-help').textContent, /within 10 km/);
  setThreshold(10);
  assert.equal(node('trigger-probability').textContent, '21.2%', 'trigger uses the observed share of years, not the Poisson rate');
  assert.match(node('trigger-explanation').textContent, /Reported fire size within 10 km at or above 10 acres/);
  node('trigger-payout').value = '10000'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$2,120.00');
  assert.match(node('pool-calculation').textContent, /21\.2% × \$10,000\.00.*observed annual frequency.*1992–2024/);
  proximityCurve(fireCurve(5));
  assert.match(node('trigger-help').textContent, /estimated from 2 source levels/);
  assert.equal(threshold.value, '10', 'changing radius keeps the chosen size');
  assert.equal(node('trigger-probability').textContent, '6.1%');
  assert.equal(node('pool-contribution').textContent, '$610.00');
  setThreshold(500);
  assert.equal(node('trigger-probability').textContent, '≤ 6.1%', 'sizes beyond the largest reported fire are an upper bound, never extrapolated');
  assert.match(node('trigger-explanation').textContent, /Upper bound from the observed record, not an estimate/);
  assert.match(node('trigger-method').textContent, /Above the highest observed level \(10 acres\).*upper bound/);
  assert.equal(node('pool-contribution').textContent, '≤ $610.00');
  assert.match(node('pool-calculation').textContent, /upper-bound frequency/);
  context.FireProximity.loaded = true;
  proximityCurve(null, 'Burned-area trigger levels are unavailable for 7 km.');
  assert.equal(node('trigger-distance').disabled, false, 'records loaded but no curve at this distance: distance stays editable');
  context.FireProximity.loaded = false;
  proximityCurve(null, 'Annual fire frequency unavailable: boom');
  assert.equal(threshold.disabled, true);
  assert.equal(node('trigger-distance').disabled, true, 'a failed lookup greys out distance as well as size');
  assert.match(node('trigger-help').textContent, /boom/);
  assert.equal(requests.length, requestsBeforeHistory, 'history hazard does not request a modeled hazard curve or a monitor table');
  assert.equal(fireLoads.length, 1, 'selecting history reuses the current map selection');
  assert.equal(proximityLoads.length, 1, 'selecting history calculates annual fire frequencies');
  assert.equal(fireLoads[0][1].lat, 30.1);
  events.click({latlng: {lat: 31, lng: -98}});
  assert.equal(fireLoads.length, 2);
  assert.equal(fireLoads[1][1].lat, 31);
  assert.equal(proximityLoads[1][1].lat, 31);
  const proximityClearsBeforeSwitch = proximityClears.length;
  const clearsBeforeSwitch = fireClears.length;
  node('disaster-select').value = 'rainfall'; node('disaster-select').change();
  assert.equal(node('nearest-fire-panel').hidden, true);
  assert.equal(node('satellite-fire-panel').hidden, true);
  assert.equal(node('trigger-result').hidden, false);
  assert.ok(fireClears.length > clearsBeforeSwitch, 'switching hazards cancels and clears history');
  assert.equal(fireLoads.length, 2, 'other hazards do not restart fire requests');
  assert.equal(proximityLoads.length, 2, 'other hazards do not restart annual frequency requests');
  assert.ok(proximityClears.length > proximityClearsBeforeSwitch);
  // Each hazard uses its own monitoring network, and the n box sets how many stations.
  node('selection-mode').value = 'point'; node('selection-mode').change();
  const hazardStation = {id: 'TEST-1', name: '<script>Gauge</script>', latitude: 31.1, longitude: -98.1,
    distance_km: 2.5, url: 'https://waterdata.usgs.gov/monitoring-location/USGS-TEST/'};
  function resolveHazardStation(request, peril, network, station = hazardStation) {
    request.resolve({ok: true, json: async () => multiStation(peril, network, station ? [station] : [])});
  }
  const metarBefore = stationRequests.length;
  for (const [peril, stationPeril, network] of [
    ['flood', 'flood', 'USGS stream gauges'], ['rainfall', 'rainfall', 'USGS precipitation gauges'],
    ['earthquake', 'earthquake', 'EarthScope seismic stations'], ['nearest_fire', 'wildfire', 'NIFC RAWS'],
    ['cyclone', 'cyclone', 'NOAA METAR wind stations'],
  ]) {
    node('disaster-select').value = peril; node('disaster-select').change();
    const request = hazardStationRequests.at(-1);
    const params = new URL(request.url, 'http://localhost').searchParams;
    assert.equal(params.get('peril'), stationPeril);
    assert.equal(params.has('network'), false);
    assert.equal(params.get('n'), '5');
    resolveHazardStation(request, stationPeril, network);
    await flush();
    assert.match(node('hazard-station-status').textContent, new RegExp(`^${network}: 1 of 5`));
    assert.equal(listedStations()[0].children[0].textContent, '<script>Gauge</script> (TEST-1) · 2.50 km');
    assert.equal(node('hazard-station-show').hidden, false);
    node('hazard-station-show').click();
    assert.equal(map.fitted.points.length, 2);
    assert.equal(map.fitted.points[1].lat, hazardStation.latitude);
  }
  assert.equal(stationRequests.length, metarBefore, 'no hazard except Heat requests the METAR weather list');
  const oldHazardMarker = [...layers].find(l => l.popup?.textContent?.startsWith('NOAA METAR wind stations:'));
  node('disaster-select').value = 'flood'; node('disaster-select').change();
  const oldHazardRequest = hazardStationRequests.at(-1);
  assert.ok(!layers.has(oldHazardMarker), 'switching hazards immediately removes the previous hazard marker');
  node('disaster-select').value = 'earthquake'; node('disaster-select').change();
  assert.equal(oldHazardRequest.options.signal.aborted, true);
  resolveHazardStation(oldHazardRequest, 'flood', 'Wrong old gauge');
  await flush();
  assert.match(node('hazard-station-status').textContent, /Finding.*EarthScope/);
  resolveHazardStation(hazardStationRequests.at(-1), 'earthquake', 'EarthScope', null);
  await flush();
  assert.match(node('hazard-station-status').textContent, /No eligible station/);
  assert.equal(node('hazard-station-show').hidden, true);
  node('hazard-station-retry').click();
  hazardStationRequests.at(-1).resolve({ok: false, status: 502, json: async () => ({error: {message: 'Provider offline'}})});
  await flush();
  assert.match(node('hazard-station-status').textContent, /EarthScope.*Provider offline/);
  assert.equal(node('hazard-station-retry').hidden, false);
  node('hazard-station-retry').click();
  const timedHazardRequest = hazardStationRequests.at(-1);
  [...timers.values()].filter(t => t.ms === 15000).at(-1).fn();
  assert.equal(timedHazardRequest.options.signal.aborted, true);
  resolveHazardStation(timedHazardRequest, 'earthquake', 'Too late');
  await flush();
  assert.match(node('hazard-station-status').textContent, /timed out/);
  node('disaster-select').value = 'cyclone'; node('disaster-select').change();
  assert.match(hazardStationRequests.at(-1).url, /peril=cyclone/, 'wind looks up its own METAR wind stations');
  const beforeHeat = hazardStationRequests.length;
  assert.equal(node('hazard-station-show').hidden, true);
  node('disaster-select').value = 'heat'; node('disaster-select').change();
  assert.equal(hazardStationRequests.length, beforeHeat, 'heat uses the actual historical curve station, not a separate nearest lookup');
  assert.match(node('hazard-station-status').textContent, /Waiting.*GHCN-Daily/);
  node('disaster-select').value = 'auto'; node('disaster-select').change();
  assert.match(node('hazard-station-status').textContent, /Waiting.*automatic/);
  requests.at(-1).resolve({ok: true, json: async () => ({peril: 'rainfall', status: 'available', points: [], selection: {explanation: 'Selected rain'}})});
  await flush();
  assert.match(hazardStationRequests.at(-1).url, /peril=rainfall/);
  resolveHazardStation(hazardStationRequests.at(-1), 'rainfall', 'USGS precipitation gauges', {...hazardStation, url: 'javascript:alert(1)'});
  await flush();
  assert.equal(listedStations()[0].children.length, 2, 'unsafe provider URL is omitted');
  node('disaster-select').value = 'rainfall'; node('disaster-select').change();
  const pendingHazardLookup = requests.at(-1);
  assert.match(pendingHazardLookup.url, /^\/api\/(v1\/)?(hazards|disaster)/);
  node('address-input').value = 'Unknown address';
  addressSearch = node('address-search').submit({preventDefault() {}});
  assert.equal(node('hazard-station-details').children.length, 0, 'address search immediately clears hazard station details');
  assert.equal(node('hazard-station-show').hidden, true);
  requests.at(-1).resolve({ok: true, json: async () => ({status: 'no_match'})});
  await addressSearch;
  assert.match(node('hazard-station-status').textContent, /not searched/);
  assert.equal(pendingHazardLookup.options.signal.aborted, false, 'a failed address search keeps the current point\'s hazard lookup');
  pendingHazardLookup.resolve({ok: true, json: async () => data});
  await flush();
  assert.match(node('hazard-status').textContent, /severity–frequency points loaded/, 'hazard view recovers instead of staying on Loading');
  // Satellite fire detections: a radius-based observed curve that is never extrapolated.
  node('satellite-radius').value = '25';
  node('disaster-select').value = 'satellite_fire'; node('disaster-select').change();
  assert.equal(node('satellite-radius-control').hidden, false, 'satellite fire shows its radius selector');
  assert.equal(node('satellite-fire-panel').hidden, false, 'live satellite detections stay visible');
  const satelliteRequest = requests.at(-1);
  assert.match(satelliteRequest.url, /^\/api\/disaster\?(?=.*peril=satellite_fire)(?=.*radius_km=25)/);
  const satellite = {peril: 'satellite_fire', status: 'available', unit: 'MW', metric: 'Strongest satellite fire detection within 25 km', radius_km: 25,
    frequency_basis: 'historical_annual_frequency', notes: [], details: [],
    points: [{severity: 4.5, frequency_pct: 100, hit_years: 13, exposure_years: 13}, {severity: 54.25, frequency_pct: 100 / 13, hit_years: 1, exposure_years: 13}],
    annual_detections: Array.from({length: 13}, (_, i) => ({year: 2013 + i, detections: i ? 20 : 0, max_frp_mw: i ? 10 : null, max_date: i ? `${2013 + i}-03-01` : null}))};
  satelliteRequest.resolve({ok: true, json: async () => satellite});
  await flush();
  assert.equal(node('satellite-years-details').hidden, false);
  assert.equal(node('satellite-years-rows').children.length, 13, 'every year is listed, including years without detections');
  assert.match(node('satellite-years-context').textContent, /12 of 13 years.*25 km/);
  assert.match(node('trigger-result-label').textContent, /Observed annual satellite fire-detection frequency/);
  // Each satellite is a monitor with its own MW record; threshold and radius follow the main controls.
  const satelliteMonitors = requests.at(-1);
  assert.match(satelliteMonitors.url, /^\/api\/satellite-monitors\?(?=.*radius_km=25)/);
  assert.doesNotMatch(satelliteMonitors.url, /[?&]n=/, 'Stations (n) does not apply to satellites');
  const satRow = (id, name, start, maxima) => ({id, name, latitude: null, longitude: null, distance_km: null, period: {start, end: 2025},
    overpass: 'about 1:30 pm', status: maxima.length >= 10 ? 'available' : 'insufficient_years', counted_years: maxima.length,
    years_with_detection: maxima.filter(v => v > 0).length, annual_maxima: maxima, median: 10, highest: Math.max(...maxima)});
  satelliteMonitors.resolve({ok: true, json: async () => ({kind: 'satellite', radius_km: 25, unit: 'MW', requested_stations: 3, station_limit: 3,
    stations_within_radius: 3, minimum_years: 10, year_rule: 'Each complete calendar year counts once.', notes: ['Satellites are not combined.'],
    stations: [
      satRow('viirs_snpp', 'Suomi NPP VIIRS (375 m pixels)', 2013, [60, ...Array(12).fill(10)]),
      satRow('viirs_noaa20', 'NOAA-20 VIIRS (375 m pixels)', 2019, [0, 5, 70, 10, 10, 10, 10]),
      satRow('modis_terra', 'Terra MODIS (1 km pixels)', 2001, [...Array(5).fill(0), 80, ...Array(19).fill(8)])]})});
  await flush();
  const satRows = () => node('records-rows').children;
  assert.equal(satRows().length, 4, 'comparison row plus one row per satellite');
  assert.match(node('records-status').textContent, /3 satellites, each counted separately within 25 km.*Stations \(n\) does not apply/);
  assert.equal(node('records-threshold-wrap').hidden, true, 'MW follows the main trigger threshold');
  assert.match(satRows()[0].children[5].textContent, /chance per year at this fire intensity · from Suomi NPP/);
  assert.equal(node('trigger-threshold-name').textContent, 'Fire intensity', 'satellite fire asks for a fire intensity, not a trigger');
  assert.match(node('trigger-heading').textContent, /Set a fire intensity/);
  assert.match(satRows()[1].children[0].textContent, /^1\. Suomi NPP VIIRS \(375 m pixels\) · 2013–2025; detections in 13 of 13 years; passes/, 'satellites show no internal id');
  assert.equal(satRows()[1].children[1].textContent, '—', 'satellites have no distance');
  setThreshold(50);
  assert.match(satRows()[1].children[5].textContent, /^7\.7% \(1\/13 years\)$/);
  assert.match(satRows()[2].children[5].textContent, /^14\.3% \(1\/7 years\) ⚠ Short record/, 'NOAA-20 has only 7 complete years');
  assert.match(satRows()[3].children[5].textContent, /^4% \(1\/25 years\)$/, 'years without detections stay in the denominator');
  setThreshold(100);
  assert.match(node('trigger-probability').textContent, /^≤ /, 'above the strongest observed detection is an upper bound, not extrapolated');
  const beforeBadRadius = requests.length;
  node('satellite-radius').value = '150'; node('satellite-radius').change();
  assert.equal(requests.length, beforeBadRadius, 'an out-of-range radius does not reload');
  assert.equal(node('satellite-radius').attributes['aria-invalid'], 'true');
  assert.match(node('satellite-radius-help').textContent, /0\.5 to 100 km/);
  node('satellite-radius').value = '12.5'; node('satellite-radius').change();
  assert.match(requests.at(-1).url, /radius_km=12\.5/, 'a custom radius reloads the curve');
  assert.equal(node('satellite-radius').attributes['aria-invalid'], 'false');
  assert.equal(node('satellite-years-details').hidden, true, 'the old years table clears while the new radius loads');
  node('disaster-select').value = 'flood'; node('disaster-select').change();
  assert.equal(node('trigger-threshold-name').textContent, 'Intensity', 'other hazards ask for an intensity');
  assert.match(node('trigger-payout-name').textContent, /Payout if this intensity is reached/);
  // Flood gauges: each NWS gauge is compared with its own flood stage for the chosen level.
  const floodHazard = requests.filter(request => /^\/api\/disaster\?.*peril=flood/.test(request.url)).at(-1);
  floodHazard.resolve({ok: true, json: async () => ({peril: 'flood', status: 'available', unit: 'm', points: [{severity: 0.3, frequency_pct: 1}]})});
  await flush();
  const floodRecords = requests.filter(request => /^\/api\/station-records\?(?=.*kind=flood)/.test(request.url)).at(-1);
  assert.ok(floodRecords, 'point flood requests the gauge table');
  assert.equal(node('records-level-wrap').hidden, false, 'flood shows a flood-level menu');
  assert.equal(node('records-threshold-wrap').hidden, true, 'no cfs threshold box');
  const gaugeRow = (id, stages, maxima, extra = {}) => ({id, name: `${id} gauge (USGS 0800)`, latitude: 29.4, longitude: -98.5, distance_km: 3,
    status: 'available', counted_years: maxima.length, period: {start: 1990, end: 2025}, annual_maxima: maxima, median: 6, highest: Math.max(...maxima),
    flood_stages: stages, ...extra});
  floodRecords.resolve({ok: true, json: async () => ({kind: 'flood', requested_stations: 5, station_limit: 10, stations_within_radius: 2, radius_km: 100,
    minimum_years: 20, year_rule: 'One highest river level per water year.', notes: [], stations: [
      gaugeRow('NEAR1', {minor: 10, moderate: 15, major: 20}, [...Array(18).fill(5), 11, 12, 16, 22]),
      gaugeRow('FAR01', {minor: 12}, [...Array(20).fill(5), 13], {datum_warning: true, datum_check_ft: 9.2})]})});
  await flush();
  node('records-level').value = 'minor';
  node('records-level').selectedOptions = [{textContent: 'Minor flood stage'}];
  node('records-level').change();
  const floodRows = () => node('records-rows').children;
  assert.match(node('records-frequency-heading').textContent, /Years reaching minor flood stage/);
  assert.match(floodRows()[0].children[5].textContent, /1% chance per year at this intensity · water depth at your point/);
  assert.match(floodRows()[1].children[0].textContent, /minor flood stage 10 ft/);
  assert.match(floodRows()[1].children[5].textContent, /^18\.2% \(4\/22 years\)$/, 'each gauge uses its own minor stage');
  assert.match(floodRows()[2].children[0].textContent, /⚠ NWS and USGS levels differ by up to 9\.2 ft/);
  node('records-level').value = 'major'; node('records-level').selectedOptions = [{textContent: 'Major flood stage'}]; node('records-level').change();
  assert.match(floodRows()[1].children[5].textContent, /^4\.5% \(1\/22 years\)$/);
  assert.match(floodRows()[2].children[5].textContent, /No major stage set for this gauge/);
  node('disaster-select').value = 'nearest_fire'; node('disaster-select').change();
  assert.equal(node('trigger-threshold-name').textContent, 'Fire size', 'nearby fire is measured in acres, a size');
  node('disaster-select').value = 'satellite_fire'; node('disaster-select').change();
  console.log('Hazard stations: every network, actual historical heat station, auto selection, markers, safe links, empty/error/timeout states and stale response clearing passed.');
  console.log('Nearest fire hazard: typed size threshold, existing/new point lookup, burned-area triggers and payouts per radius, and clearing on hazard switch passed.');
  console.log('Historical Heat: selector/API flow, station/baseline/coverage, triggers and historical contributions, CSV provenance, point-only areas and stale-data clearing passed.');
  console.log('Heat screening: units, bands, timestamps, stale/absent values, safe text, empty/error/timeout states, refresh and hazard switching passed.');
  console.log('Hazard station lists: n stations per hazard network, no METAR list outside Heat, address flow, count validation, partial/empty results, all map markers, safe links and stale-response handling passed.');
  console.log('Native fireline intensity: units, probability/payout exclusion, missing coverage, CSV provenance, area samples and hazard switching passed.');
  console.log('Trigger threshold: area/point prefill, typed thresholds kept per hazard, interpolation/extrapolation/bounds, payouts, ranges, historical zeros, invalid inputs, site-class resets, and unavailable data passed.');
  console.log('Area UI: arithmetic means, per-point table/markers, trigger changes, payout, all-threshold CSV, missing/zero values, and stale-response protection passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
