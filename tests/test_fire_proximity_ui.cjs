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
    frequency_pct: (i + 1) / 33 * 100, reference_interval: {minimum_pct: 1, maximum_pct: 25}})), annual_records: annual};
async function resolve(request, value = {data}, ok = true) { request.resolve({ok, json: async () => value}); await flush(); }

(async () => {
  context.FireProximity.load(map, {lat: 29.4241, lng: -98.4936});
  assert.match(pending[0].url, /^\/api\/v1\/fire-proximity\?lat=29.4241&lon=-98.4936$/);
  await resolve(pending[0]);
  assert.equal(node('fire-proximity-value').textContent, '6.1%');
  assert.match(node('fire-proximity-calculation').textContent, /2 of 33.*10 km/);
  assert.equal(node('fire-proximity-rows').children.length, 3);
  assert.equal(node('fire-proximity-years').children.length, 33);
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
  console.log('Annual fire proximity: year counts, frequencies, reference intervals, radius rings, chart, safe auditable CSV, stale responses, provider failure, unavailable coverage, timeout and clearing passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
