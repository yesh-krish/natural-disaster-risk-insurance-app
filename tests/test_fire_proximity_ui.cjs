const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const nodes = new Map(), pending = [], timers = new Map(), layers = new Set(), charts = [], blobs = [], downloads = [], events = {};
function node(id = '') {
  if (nodes.has(id)) return nodes.get(id);
  const element = {children: [], dataset: {}, hidden: false, disabled: false, textContent: '', value: '',
    append(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
    addEventListener(name, fn) { this[name] = fn; }, click() { downloads.push(this); }, remove() {} };
  if (id) nodes.set(id, element);
  return element;
}
node('fire-proximity-radius').value = '10';
const map = {removeLayer(layer) { layers.delete(layer); }, fitBounds(bounds) { this.bounds = bounds; }};
const document = {hidden: false, body: node(), getElementById: node, createElement: () => node(), addEventListener(name, fn) { events[name] = fn; }};
class LocalURL extends URL { static createObjectURL(blob) { blobs.push(blob); return 'blob:csv'; } static revokeObjectURL() {} }
const context = vm.createContext({document, AbortController, URL: LocalURL, URLSearchParams, Blob,
  setTimeout(fn, ms) { const id = Symbol(); timers.set(id, {fn, ms}); return id; }, clearTimeout(id) { timers.delete(id); },
  fetch(url, options) { return new Promise((resolve, reject) => pending.push({url, options, resolve, reject})); },
  drawHazardChart(...args) { charts.push(args); },
  L: {circle(point, options) { return {point, options, addTo() {layers.add(this); return this;}, getBounds() {return {radius: options.radius};}}; }},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../fire_proximity.js'), 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const annual = Array.from({length: 33}, (_, index) => {
  const year = 1992 + index, distance = {2000: 2, 2001: 7, 2002: 18}[year];
  return {year, nearest_distance_within_20_km: distance ?? null,
    nearest_fire: distance ? {name: '=HYPERLINK("bad")', id: String(year), discovery_date: `${year}-01-01`, record_url: 'https://apps.fs.usda.gov/record'} : null,
    within_radii: Object.fromEntries([5, 10, 20].map(radius => [String(radius), distance != null && distance <= radius]))};
});
const data = {status: 'available', frequency_basis: 'historical_annual_reported_fire_frequency', baseline: {start_year: 1992, end_year: 2024, year_count: 33, source_layer: 'Fire_FPA_FOD_7th_Fires', coverage_assumption: 'Zero-record years remain; local coverage not independently verified.'},
  county: {name: 'Bexar', state: 'Texas'}, checked_at: '2026-11-05T00:00:00Z', source_url: 'https://apps.fs.usda.gov/archive',
  frequencies: [5, 10, 20].map((radius, i) => ({radius_km: radius, years_with_fire: i + 1, total_years: 33,
    frequency_pct: (i + 1) / 33 * 100, reference_interval: {minimum_pct: 1, maximum_pct: 25}})), annual_records: annual,
  size_exceedance: {size_thresholds_acres: [10, 100, 1000, 5000],
    area_exceedance: {curves: [5, 10, 20].map(radius => ({radius_km: radius, fire_count: radius, total_years: 33, largest_reported_acres: 60, expected_annual_reported_acres: 9.9,
      points: [{min_acres: 1, fires_at_or_above: 223, expected_annual_exceedances: 6.758, annual_probability_pct: 99.9, empirical_years_pct: 45.5, return_period_years: 1.0012},
               {min_acres: 10, fires_at_or_above: 3, expected_annual_exceedances: 0.091, annual_probability_pct: 8.7, empirical_years_pct: 6.1, return_period_years: 11.5},
               {min_acres: 100, fires_at_or_above: 0, expected_annual_exceedances: 0, annual_probability_pct: 0, empirical_years_pct: 0, return_period_years: null}]}))},
    threshold_frequencies: [10, 100, 1000, 5000].flatMap(acres => [5, 10, 20].map(radius => ({min_acres: acres, radius_km: radius, years_with_fire: acres === 10 ? 2 : 0, total_years: 33, frequency_pct: acres === 10 ? 2 / 33 * 100 : 0}))),
    footprint_reach: {years_with_reach: 0, total_years: 33, fires: [], closest_footprint_gap_km: 0.5624},
    burned_area: [{radius_km: 5, overlapping_reported_acres: 327.3, total_years: 33, mean_annual_burned_share_pct: 0.0511, equivalent_return_interval_years: 1957}]}};
async function resolve(request, value = {data}, ok = true) { request.resolve({ok, json: async () => value}); await flush(); }

(async () => {
  context.FireProximity.load(map, {lat: 29.4241, lng: -98.4936});
  assert.match(pending[0].url, /^\/api\/v1\/fire-proximity\?lat=29.4241&lon=-98.4936$/);
  await resolve(pending[0]);
  assert.equal(node('fire-proximity-value').textContent, '6.1%');
  assert.match(node('fire-proximity-calculation').textContent, /2 of 33.*10 km/);
  assert.equal(node('fire-proximity-rows').children.length, 3);
  assert.equal(node('fire-proximity-years').children.length, 33);
  assert.equal(node('fire-proximity-size').hidden, false);
  assert.equal(node('fire-proximity-curve-rows').children.length, 3);
  assert.match(node('fire-proximity-curve-rows').children[1].children.map(cell => cell.textContent).join(' | '), /10 acres.*0\.091.*8\.7%.*6\.1%.*1 in 11\.5 years/);
  assert.match(node('fire-proximity-curve-rows').children[2].children.map(cell => cell.textContent).join(' | '), /Not observed/);
  assert.match(node('fire-proximity-curve-summary').textContent, /Within 10 km: 10 reported fires, about 9\.9 reported acres per year/);
  assert.match(node('fire-proximity-curve-chart').innerHTML, /<svg[^>]*Burned-area exceedance curve/);
  assert.equal(node('fire-proximity-size-rows').children.length, 4);
  assert.match(node('fire-proximity-size-rows').children[0].children[1].textContent, /2 \/ 33 \(6\.1%\)/);
  assert.match(node('fire-proximity-reach').textContent, /closest footprint edge was 0\.56 km/);
  assert.match(node('fire-proximity-burned').textContent, /0\.051% of the circle per year.*1,957 years/);
  assert.match(node('fire-proximity-years').children[0].children[1].textContent, /No eligible record/);
  assert.match(node('fire-proximity-years').children[8].children[1].textContent, /=HYPERLINK/);
  assert.equal(charts[0][3], 'Historical annual frequency (%)');
  assert.equal(layers.size, 1);
  assert.equal([...layers][0].options.radius, 10000);
  node('fire-proximity-radius').value = '5'; node('fire-proximity-radius').change();
  assert.equal(node('fire-proximity-value').textContent, '3.0%');
  assert.equal([...layers][0].options.radius, 5000);
  assert.equal(layers.size, 1);
  assert.equal(node('fire-proximity-rows').children[0].dataset.selected, 'true');
  // Any trigger distance up to 20 km: computed from sized_fires rows, never interpolated between radii.
  const curves = [];
  context.FireProximity.onCurve(detail => curves.push(detail));
  data.size_exceedance.sized_fires = {fields: ['year', 'distance_km', 'reported_acres'], max_distance_km: 20,
    rows: [[2000, 2, 0.5], [2000, 4, 150], [2001, 7, 40], [2002, 18, 6000]]};
  assert.equal(context.FireProximity.setDistance(7.5), null);
  assert.equal(context.FireProximity.distance, 7.5);
  assert.equal([...layers][0].options.radius, 7500, 'ring follows the trigger distance');
  assert.equal(node('fire-proximity-value').textContent, '6.1%');
  assert.match(node('fire-proximity-calculation').textContent, /2 of 33 .*within 7\.5 km/);
  assert.ok([...node('fire-proximity-rows').children].every(row => row.dataset.selected === 'false'), 'no preset row matches 7.5 km');
  const custom = curves.at(-1).curve;
  assert.equal(custom.radius_km, 7.5);
  assert.equal(custom.fire_count, 3);
  assert.equal(JSON.stringify(custom.points.map(p => [p.min_acres, Number(p.empirical_years_pct.toFixed(2))])), JSON.stringify(
    [[0.1, 6.06], [0.3, 6.06], [1, 6.06], [3, 6.06], [10, 6.06], [30, 6.06], [100, 3.03], [300, 0]]));
  assert.match(node('fire-proximity-curve-summary').textContent, /Within 7\.5 km: 3 reported fires/);
  assert.equal(context.FireProximity.setDistance(20), null);
  assert.equal(node('fire-proximity-radius').value, '20', 'preset distances sync the radius menu');
  assert.equal(curves.at(-1).curve.largest_reported_acres, 6000);
  for (const bad of [0, -2, 20.5, NaN]) assert.match(context.FireProximity.setDistance(bad), /above 0 and up to 20 km/);
  assert.equal(context.FireProximity.distance, 20, 'invalid distances are not applied');
  node('fire-proximity-radius').value = '5'; node('fire-proximity-radius').change();
  assert.equal(curves.at(-1).curve.radius_km, 5);
  assert.equal(context.FireProximity.distance, 5);
  node('fire-proximity-show').click();
  assert.equal(map.bounds.radius, 5000);
  node('fire-proximity-export').click();
  const csv = await blobs.at(-1).text();
  assert.equal(csv.trim().split('\r\n').length, 103);
  assert.match(csv, /"summary","29.4241","-98.4936","1992","2024","5"/);
  assert.match(csv, /'=HYPERLINK/);
  assert.match(csv, /historical_annual_reported_fire_frequency/);
  assert.match(csv, /Fire_FPA_FOD_7th_Fires/);
  assert.match(csv, /local coverage not independently verified/);
  assert.match(downloads.at(-1).download, /^fire_proximity_29.4241_-98.4936\.csv$/);

  node('fire-proximity-refresh').click();
  const stale = pending.at(-1);
  context.FireProximity.load(map, {lat: 30, lng: -99}, true);
  assert.equal(stale.options.signal.aborted, true);
  assert.equal(layers.size, 0);
  await resolve(pending.at(-1));
  assert.match(node('fire-proximity-calculation').textContent, /area center/);
  await resolve(stale, {data: {...data, status: 'unavailable', reason: 'Stale error'}});
  assert.equal(node('fire-proximity-content').hidden, false);

  [...timers.values()].find(timer => timer.ms === 300000).fn();
  await resolve(pending.at(-1), {error: {message: 'Provider offline'}}, false);
  assert.match(node('fire-proximity-status').textContent, /unavailable.*Provider offline/);
  assert.equal(node('fire-proximity-content').hidden, true);
  assert.equal(node('fire-proximity-export').disabled, true);
  assert.equal(layers.size, 0);
  node('fire-proximity-refresh').click();
  await resolve(pending.at(-1), {data: {status: 'unavailable', reason: 'Outside US coverage'}});
  assert.match(node('fire-proximity-status').textContent, /Outside US coverage/);
  assert.equal(node('fire-proximity-content').hidden, true);
  node('fire-proximity-refresh').click();
  const timed = pending.at(-1);
  [...timers.values()].find(timer => timer.ms === 55000).fn();
  timed.reject(new Error('Aborted')); await flush();
  assert.match(node('fire-proximity-status').textContent, /timed out/);
  assert.equal(node('fire-proximity-refresh').disabled, false);
  context.FireProximity.clear();
  assert.equal(node('fire-proximity-refresh').disabled, true);
  const count = pending.length;
  events.visibilitychange();
  assert.equal(pending.length, count);
  console.log('Annual fire proximity: year counts, frequencies, reference intervals, radius rings, any trigger distance up to 20 km, chart, safe auditable CSV, stale responses, provider failure, unavailable coverage, timeout and clearing passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
