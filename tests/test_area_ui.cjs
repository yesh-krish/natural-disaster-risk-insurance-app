const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the complete application script with deterministic DOM, map, and API adapters.
const nodes = new Map(), events = {}, layers = new Set(), requests = [], downloads = [];
const stationRequests = [], timers = new Map();
function node(id = '') {
  if (id && nodes.has(id)) return nodes.get(id);
  const n = { value: '', textContent: '', hidden: false, disabled: false, children: [], dataset: {},
    validity: { valid: true }, classList: { add() {}, remove() {} },
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
  fetch(url, options) { return new Promise(resolve => (url.startsWith('/api/v1/monitoring-stations?') ? stationRequests : requests).push({ url, options, resolve })); },
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
  const comparisonRows = () => node('comparison-rows').children;
  const comparisonButton = index => comparisonRows()[index].children.at(-1).children[0];
  assert.equal(node('comparison-content').hidden, false);
  assert.equal(comparisonRows().length, 2);
  assert.equal(comparisonRows()[0].children[1].textContent, '13%');
  assert.equal(comparisonRows()[0].children[3].textContent, '—', 'probabilities are visible before a payout is entered');
  assert.match(comparisonRows()[1].children[4].textContent, /20\/25 used; 5 unavailable/);
  assert.match(node('comparison-frequency').textContent, /Average sampled/);
  assert.match(node('comparison-note').textContent, /not the probability of an event anywhere/);
  assert.equal(comparisonButton(0).attributes['aria-pressed'], 'true');
  node('trigger-payout').value = '10000'; node('trigger-payout').input();
  assert.equal(comparisonRows()[0].children[3].textContent, '$1,300.00');
  assert.equal(comparisonRows()[1].children[3].textContent, '$950.00');
  assert.equal(node('pool-contribution').textContent, '$1,300.00');
  assert.match(node('pool-calculation').textContent, /average/);
  const firstMarkers = [...layers].filter(l => /^Sample:/.test(l.popup || ''));
  assert.equal(firstMarkers.length, 25);
  comparisonButton(1).click();
  assert.equal(node('trigger-select').value, '1', 'comparison buttons control the existing trigger');
  assert.equal(comparisonButton(1).attributes['aria-pressed'], 'true');
  assert.equal(comparisonButton(0).attributes['aria-pressed'], 'false');
  assert.equal(node('trigger-probability').textContent, '9.5%');
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
  assert.equal(comparisonRows().length, 0, 'old comparison rows clear immediately when the area changes');
  assert.equal(node('comparison-content').hidden, true);
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
  assert.equal(node('trigger-select').disabled, true);
  assert.match(node('trigger-help').textContent, /Restart the Python server/);
  assert.doesNotMatch(node('trigger-help').textContent, /government|TWDB|coverage/);
  node('redraw-area').click();
  events.click({ latlng: { lat: 0, lng: 0 } }); events.click({ latlng: { lat: 4, lng: 4 } });
  requests[4].resolve({ ok: true, status: 200, json: async () => data });
  await flush();
  assert.equal(node('trigger-select').disabled, false, 'area triggers recover after updated backend is available');
  assert.equal(node('trigger-probability').textContent, '13%');
  node('redraw-area').click();
  events.click({ latlng: { lat: 0, lng: 0 } }); events.click({ latlng: { lat: 4, lng: 4 } });
  requests[5].resolve({ ok: true, status: 200, json: async () => ({...data, area: {...data.area, aggregation: undefined}}) });
  await flush();
  assert.equal(node('trigger-select').disabled, true, 'an outdated maximum response must never be labeled as an average');
  assert.match(node('trigger-help').textContent, /updated backend/);
  const stationData = {data: { status: 'available', network: 'NOAA AWC METAR', message: 'Network context',
    requested_count: 5, returned_count: 2, search_radius_km: 200, fewer_than_requested: true,
    coverage: 'METAR stations within 200 km', catalog_fetched_at: '2026-11-03T00:00:00Z',
    stations: [
      { id: 'KAAA', name: 'First airport', latitude: 2.1, longitude: 2.1, distance_km: 1.23,
        network: 'NOAA AWC METAR', measured_variables: ['Air temperature', 'Wind'], variables_note: 'Report availability varies.',
        observation: {status: 'stale', observed_at: '2026-11-02T20:00:00Z', age_minutes: 180,
          values: [{variable: 'Wind speed', value: 0, unit: 'kn'}], message: 'Older than two hours.',
          heat: {status: 'unavailable', air_temperature_c: 0, heat_index_f: null, category: null, reason: 'Report is older than 2 hours; Heat Index withheld.'}},
        url: 'https://aviationweather.gov/data/metar/?id=KAAA'},
      { id: 'KBBB', name: '<script>Airport</script>', latitude: 2.2, longitude: 2.2, distance_km: 2.34,
        network: 'NOAA AWC METAR', measured_variables: ['Wind'], variables_note: 'Report availability varies.',
        observation: {status: 'available', observed_at: '2026-11-02T22:59:00Z', age_minutes: 1,
          values: [], message: 'Latest report.',
          heat: {status: 'available', air_temperature_c: 37.7777778, relative_humidity_pct: 55,
            heat_index_c: 51.1, heat_index_f: 124, category: 'Danger', reason: 'Shade screening only; not WBGT.'}},
        url: 'https://aviationweather.gov/data/metar/?id=KBBB'},
    ] }};
  stationRequests.at(-1).resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('station-status').textContent, /2 of 5.*area center.*Fewer eligible/);
  assert.equal(node('station-list').children.length, 2);
  assert.match(node('station-list').children[0].children[0].textContent, /First airport.*1.23 km/);
  assert.match(node('station-list').children[0].children[2].textContent, /STALE.*UTC.*0 kn/);
  assert.equal(node('station-list').children[1].children[0].textContent, '<script>Airport</script> (KBBB) · 2.34 km');
  assert.equal(node('station-list').children[0].children[3].href, 'https://aviationweather.gov/data/metar/?id=KAAA');
  assert.match(node('heat-status').textContent, /2 nearby stations.*No area average/);
  assert.equal(node('heat-list').children.length, 2);
  assert.match(node('heat-list').children[0].children[1].textContent, /0.0 °C.*32.0 °F.*UTC.*180 minutes.*Heat Index unavailable.*older than 2 hours/);
  assert.match(node('heat-list').children[1].children[1].textContent, /37.8 °C.*100.0 °F.*55.0%.*51.1 °C.*124.0 °F.*Danger.*not WBGT/);
  assert.equal(node('heat-list').children[1].children[0].textContent, '<script>Airport</script> (KBBB) · 2.34 km');
  const stationLayer = [...layers].find(l => l.popup?.textContent?.startsWith('1. First airport'));
  assert.ok(stationLayer, 'station has a separate map highlight');
  assert.ok([...layers].some(l => l.popup?.textContent?.startsWith('2. <script>Airport')));
  node('station-show').click();
  assert.equal(map.fitted.points.length, 3, 'fit includes selection and every station');
  node('redraw-area').click();
  events.click({ latlng: { lat: 1, lng: 1 } }); events.click({ latlng: { lat: 3, lng: 3 } });
  assert.ok(!layers.has(stationLayer), 'new location removes old station immediately');
  assert.equal(node('station-list').children.length, 0);
  assert.equal(node('heat-list').children.length, 0, 'location changes clear heat snapshot immediately');
  stationRequests[0].resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('station-status').textContent, /Finding/, 'stale station response cannot overwrite current selection');
  stationRequests.at(-1).resolve({ok: false, status: 502, json: async () => ({error: {message: 'Station service unavailable'}})});
  await flush();
  assert.match(node('station-status').textContent, /Station service unavailable/);
  assert.match(node('heat-status').textContent, /unavailable.*failed/);
  assert.equal(node('station-retry').hidden, false);
  node('station-retry').click();
  const stalledRequest = stationRequests.at(-1);
  [...timers.values()].filter(t => t.ms === 15000).at(-1).fn();
  assert.equal(stalledRequest.options.signal.aborted, true);
  assert.match(node('station-status').textContent, /taking too long/);
  assert.match(node('heat-status').textContent, /timed out/);
  stalledRequest.resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('station-status').textContent, /taking too long/, 'late results cannot replace timeout');
  node('station-retry').click();
  stationRequests.at(-1).resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('station-status').textContent, /2 of 5/);
  assert.equal(node('station-retry').hidden, true);
  for (const peril of ['earthquake', 'wildfire']) {
    node('disaster-select').value = peril; node('disaster-select').change();
    const params = new URL(stationRequests.at(-1).url, 'http://localhost').searchParams;
    assert.equal(params.get('network'), 'awc_metar', 'multi-station network is explicit and independent of hazard');
    assert.equal(params.has('peril'), false);
    stationRequests.at(-1).resolve({ok: true, json: async () => stationData});
    await flush();
    assert.match(node('station-status').textContent, /2 of 5/);
  }
  const staleCountRequest = stationRequests.at(-1);
  node('station-count').value = '3'; node('station-count').input();
  const nextCountRequest = stationRequests.at(-1);
  assert.equal(new URL(nextCountRequest.url, 'http://localhost').searchParams.get('n'), '3');
  assert.equal(node('station-list').children.length, 0, 'count changes immediately remove old results');
  staleCountRequest.resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('station-status').textContent, /Finding/);
  const beforeInvalid = stationRequests.length;
  for (const invalid of ['', '0', '-1', '1.5', '1e1', '51']) {
    node('station-count').value = invalid; node('station-count').input();
    assert.match(node('station-status').textContent, /positive integer/);
  }
  assert.equal(stationRequests.length, beforeInvalid, 'invalid counts never call the backend');
  assert.equal(nextCountRequest.options.signal.aborted, true);
  nextCountRequest.resolve({ok: true, json: async () => stationData});
  await flush();
  assert.match(node('station-status').textContent, /positive integer/, 'aborted same-location count response is ignored');
  node('station-count').value = '5'; node('station-count').input();
  stationRequests.at(-1).resolve({ok: true, json: async () => ({data: {...stationData.data, status: 'not_found', stations: [], returned_count: 0}})});
  await flush();
  assert.match(node('station-status').textContent, /0 of 5/);
  assert.match(node('heat-status').textContent, /No eligible.*does not imply low heat hazard/);
  assert.equal(node('station-list').children.length, 0);
  assert.equal(node('station-show').hidden, true);
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
  assert.equal(comparisonRows().length, 2, 'duplicate levels merge and missing severity is omitted');
  assert.match(node('comparison-frequency').textContent, /observed annual exceedance frequency/);
  assert.match(node('comparison-note').textContent, /Zero observed years does not mean zero future risk/);
  assert.equal(node('comparison-coverage').hidden, true);
  assert.equal(comparisonRows()[0].children[1].textContent, '2–10%');
  assert.equal(comparisonRows()[0].children[3].textContent, '$200.00–$1,000.00');
  assert.equal(comparisonRows()[1].children[1].textContent, '0%');
  assert.equal(comparisonRows()[1].children[3].textContent, '$0.00');
  node('trigger-payout').value = '25000'; node('trigger-payout').input();
  assert.equal(comparisonRows()[0].children[3].textContent, '$500.00–$2,500.00');
  for (const payout of ['', '-1', 'not a number']) {
    node('trigger-payout').value = payout; node('trigger-payout').input();
    assert.equal(comparisonRows()[0].children[3].textContent, '—', 'invalid payouts clear all contributions');
    assert.equal(comparisonRows()[0].children[1].textContent, '2–10%', 'invalid payouts preserve source frequencies');
  }
  node('trigger-payout').value = '100.001'; node('trigger-payout').validity.valid = false;
  node('trigger-payout').input();
  assert.equal(comparisonRows()[0].children[3].textContent, '—', 'fractional-cent payouts do not calculate');
  node('trigger-payout').validity.valid = true;
  node('trigger-payout').value = '0'; node('trigger-payout').input();
  assert.equal(comparisonRows()[0].children[3].textContent, '$0.00', 'zero is a valid payout');
  node('trigger-select').value = '0'; node('trigger-select').change();
  assert.equal(comparisonRows()[0].dataset.selected, 'true', 'dropdown selection highlights the matching row');
  node('disaster-select').value = 'earthquake'; node('disaster-select').change();
  assert.equal(comparisonRows().length, 0);
  requests.at(-1).resolve({ok: true, json: async () => ({
    peril: 'earthquake', status: 'available', unit: '%g', points: [{severity: 12, frequency_pct: 1}],
  })});
  await flush();
  assert.match(node('comparison-status').textContent, /Only one threshold/);
  assert.match(node('comparison-context').textContent, /Site class BC/);
  node('site-class').value = 'D'; node('site-class').change();
  assert.equal(comparisonRows().length, 0, 'site-class changes clear stale comparisons');
  requests.at(-1).resolve({ok: true, json: async () => ({
    peril: 'earthquake', status: 'unavailable', points: [], reason: 'No supported data here.',
  })});
  await flush();
  assert.equal(node('comparison-content').hidden, true);
  assert.equal(comparisonRows().length, 0);
  assert.match(node('comparison-status').textContent, /No supported data here/);
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
  assert.equal(node('comparison-panel').hidden, true);
  assert.equal(node('hazard-curve-layout').hidden, true);
  assert.equal(node('hazard-intensity').hidden, false);
  assert.equal(node('intensity-value').textContent, '—', 'previous hazard clears immediately');
  requests.at(-1).resolve({ok: true, json: async () => intensity});
  await flush();
  assert.match(node('intensity-value').textContent, /71.296150208 kW\/m/);
  assert.equal(node('intensity-preview-value').textContent, node('intensity-value').textContent);
  assert.equal(node('trigger-select').disabled, true);
  assert.equal(node('trigger-probability').textContent, '—');
  assert.equal(node('pool-contribution').textContent, '—');
  assert.equal(comparisonRows().length, 0);
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
  assert.equal(node('comparison-panel').hidden, false);
  assert.equal(node('intensity-value').textContent, '—');
  assert.equal(node('export-intensity').disabled, true);
  assert.equal(node('area-sample-rows').children.length, 0);
  // Address lookup must resolve uniquely before the station request is sent.
  node('address-input').value = '100 Dolorosa St, San Antonio, TX';
  let addressSearch = node('address-search').submit({preventDefault() {}});
  assert.match(requests.at(-1).url, /^\/api\/geocode\?/);
  assert.equal(node('station-list').children.length, 0);
  requests.at(-1).resolve({ok: true, json: async () => ({status: 'matched',
    matched_address: '100 DOLOROSA, SAN ANTONIO, TX, 78205', latitude: 29.424206, longitude: -98.493261})});
  await addressSearch;
  assert.match(node('search-status').textContent, /Matched: 100 DOLOROSA/);
  let stationParams = new URL(stationRequests.at(-1).url, 'http://localhost').searchParams;
  assert.equal(stationParams.get('lat'), '29.424206');
  assert.equal(stationParams.get('lon'), '-98.493261');
  assert.equal(stationParams.get('n'), '5');
  const stationCountBeforeAmbiguity = stationRequests.length;
  for (const status of ['ambiguous', 'no_match']) {
    addressSearch = node('address-search').submit({preventDefault() {}});
    requests.at(-1).resolve({ok: true, json: async () => ({status, message: 'Refine the address.'})});
    await addressSearch;
    assert.match(node('station-status').textContent, /Stations were not searched/);
    assert.equal(stationRequests.length, stationCountBeforeAmbiguity);
    assert.equal(node('station-list').children.length, 0);
  }
  node('heat-refresh').click();
  assert.match(node('heat-status').textContent, /Loading/);
  assert.equal(node('heat-list').children.length, 0);
  const legacyStationData = JSON.parse(JSON.stringify(stationData));
  for (const station of legacyStationData.data.stations) delete station.observation.heat;
  stationRequests.at(-1).resolve({ok: true, json: async () => legacyStationData});
  await flush();
  assert.match(node('heat-list').children[0].children[1].textContent, /Heat Index unavailable.*restart the Python server/);
  node('disaster-select').value = 'cyclone'; node('disaster-select').change();
  assert.match(requests.at(-1).url, /peril=cyclone/, 'Wind keeps its existing API identifier');
  assert.equal(node('heat-list').children.length, 0, 'hazard changes clear the previous heat snapshot');
  assert.equal(node('heat-observation-panel').hidden, true, 'latest heat context is hidden for other hazards');
  node('disaster-select').value = 'heat'; node('disaster-select').change();
  assert.match(requests.at(-1).url, /^\/api\/disaster\?.*peril=heat/);
  assert.equal(node('heat-observation-panel').hidden, false);
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
  assert.equal(node('trigger-select').disabled, false);
  assert.equal(node('trigger-select').children.length, 3);
  node('trigger-select').value = '1'; node('trigger-select').change();
  node('trigger-payout').value = '30000'; node('trigger-payout').input();
  assert.equal(node('pool-contribution').textContent, '$10,000.00');
  assert.match(node('pool-calculation').textContent, /observed annual frequency.*Historical 1991–2020 station-proxy/);
  assert.match(node('trigger-result-label').textContent, /Observed annual heat exceedance frequency/);
  assert.match(node('trigger-location').textContent, /USW00012921.*13.40 km.*30 included years/);
  assert.match(node('trigger-method').textContent, /not a calibrated future probability/);
  node('export-heat').click();
  const heatCsv = await blobs.at(-1).text();
  assert.equal(heatCsv.trim().split('\r\n').length, 33, 'header, two thresholds and 30 annual maxima');
  assert.match(heatCsv, /annual_maximum,.*1991,365,365,40,104,1991-07-01,true,USW00012921/);
  assert.match(heatCsv, new RegExp('a'.repeat(64)));
  node('selection-mode').value = 'area'; node('selection-mode').change();
  events.click({latlng: {lat: 29, lng: -99}}); events.click({latlng: {lat: 30, lng: -98}});
  const unsupportedHeatArea = {peril: 'heat', status: 'unavailable', points: [], reason: 'Historical heat supports Point mode; area curves unavailable.',
    area: {aggregation: 'arithmetic_mean', sample_count: 25, available_samples: 0, samples: [], centroid: {latitude: 29.5, longitude: -98.5}}};
  requests.at(-1).resolve({ok: true, json: async () => unsupportedHeatArea});
  await flush();
  assert.match(node('hazard-status').textContent, /Point mode/);
  assert.equal(node('trigger-select').disabled, true);
  assert.equal(node('pool-contribution').textContent, '—');
  assert.equal(node('heat-history-details').hidden, true);
  assert.equal(node('export-heat').disabled, true);
  node('disaster-select').value = 'rainfall'; node('disaster-select').change();
  assert.equal(node('heat-observation-panel').hidden, true);
  assert.equal(node('heat-history-rows').children.length, 0);
  node('selection-mode').value = 'point'; node('selection-mode').change();
  node('disaster-select').value = 'potential_fire_intensity'; node('disaster-select').change();
  const potentialFire = {peril: 'potential_fire_intensity', status: 'available', unit: 'kW/m',
    metric: 'Potential surface-fire intensity', annual_probability_available: true,
    actual_wildfire_probability_available: false, source_name: 'ERA5 / LANDFIRE research model',
    frequency_label: 'Annual potential-intensity exceedance frequency, 1991–2020 (%)',
    points: [{severity: 50, frequency_pct: 100}, {severity: 100, frequency_pct: 10}],
    baseline: {complete_years: 30}, landscape: {fuel_vintage: 'LANDFIRE 2023', fuel_model: 188},
    annual_maxima: Array.from({length: 30}, (_, i) => ({year: 1991+i, complete: true, valid_hours: 8760,
      expected_hours: 8760, maximum_kw_m: 50+i, peak: {time_utc: `${1991+i}-07-01T12:00Z`}}))};
  assert.match(requests.at(-1).url, /^\/api\/potential-fire\?/);
  requests.at(-1).resolve({ok: true, status: 202, json: async () => ({status: 'processing',
    progress: {phase: 'calculating', completed_years: 6, total_years: 30}})}); await flush();
  assert.match(node('hazard-status').textContent, /6 \/ 30 years ready/);
  assert.equal(node('trigger-select').disabled, true);
  const progressTimer = [...timers.values()].filter(timer => timer.ms === 1000).at(-1);
  assert.ok(progressTimer, 'background preparation is polled without blocking the UI');
  progressTimer.fn(); await flush();
  assert.match(requests.at(-1).url, /^\/api\/potential-fire\?/);
  requests.at(-1).resolve({ok: true, status: 200, json: async () => potentialFire}); await flush();
  assert.equal(node('fire-history-rows').children.length, 30);
  assert.equal(node('hazard-curve-layout').hidden, false);
  assert.match(node('fire-history-context').textContent, /not measurements of historical fires/);
  assert.match(node('frequency-column').textContent, /potential-intensity/);
  node('trigger-select').value = '1'; node('trigger-select').change();
  node('trigger-payout').value = '10000'; node('trigger-payout').input();
  assert.equal(node('trigger-probability').textContent, '10%');
  assert.equal(node('pool-contribution').textContent, '$1,000.00');
  assert.match(node('pool-calculation').textContent, /without an actual fire/);
  assert.match(node('trigger-result-label').textContent, /potential-intensity/);
  node('export-potential-fire').click();
  const potentialExport = JSON.parse(await blobs.at(-1).text());
  assert.equal(potentialExport.actual_wildfire_probability_available, false);
  assert.equal(potentialExport.annual_maxima.length, 30);
  events.click({latlng: {lat: 30.1, lng: -97.3}});
  requests.at(-1).resolve({ok: true, status: 202, json: async () => ({status: 'processing',
    progress: {phase: 'calculating', completed_years: 2, total_years: 30}})}); await flush();
  const staleProgressTimer = [...timers.values()].filter(timer => timer.ms === 1000).at(-1);
  const fireRequestsBeforeSwitch = requests.filter(request => request.url.startsWith('/api/potential-fire?')).length;
  node('disaster-select').value = 'rainfall'; node('disaster-select').change();
  staleProgressTimer.fn(); await flush();
  assert.equal(requests.filter(request => request.url.startsWith('/api/potential-fire?')).length,
    fireRequestsBeforeSwitch, 'switching hazards stops polling the old fire point');
  assert.equal(node('fire-history-details').hidden, true);
  assert.equal(node('export-potential-fire').disabled, true);
  console.log('Potential intensity: hazard/API flow, annual maxima, conditional interpretation, triggers, illustrative payouts, provenance export and stale-data clearing passed.');
  console.log('Historical Heat: selector/API flow, station/baseline/coverage, triggers and historical contributions, CSV provenance, point-only areas and stale-data clearing passed.');
  console.log('Heat screening: units, bands, timestamps, stale/absent values, safe text, empty/error/timeout states, refresh and hazard switching passed.');
  console.log('Nearby stations: address flow, count validation, partial/empty results, all map markers, safe links, observations, and stale-response handling passed.');
  console.log('Native fireline intensity: units, probability/payout exclusion, missing coverage, CSV provenance, area samples and hazard switching passed.');
  console.log('Trigger comparison: area coverage, row/dropdown selection, shared payouts, ranges, historical zeros, invalid inputs, site-class resets, and unavailable data passed.');
  console.log('Area UI: arithmetic means, per-point table/markers, trigger changes, payout, all-threshold CSV, missing/zero values, and stale-response protection passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
