const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const nodes = new Map(), pending = [], timers = new Map(), layers = new Set();
function node(id = '') {
  if (nodes.has(id)) return nodes.get(id);
  const element = {children: [], dataset: {}, hidden: false, disabled: false, textContent: '', value: '',
    append(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
    addEventListener(name, fn) { this[name] = fn; }, click() { this.clicked = true; }};
  if (id) nodes.set(id, element);
  return element;
}
node('satellite-fire-radius').value = '25';
node('satellite-fire-window').value = '24h';
const map = {removeLayer(layer) { layers.delete(layer); }, fitBounds(bounds) { this.bounds = bounds; }};
const layer = (kind, point, options) => ({kind, point, options, addTo() { layers.add(this); return this; },
  bindPopup(popup) { this.popup = popup; return this; }});
const context = vm.createContext({document: {getElementById: node, createElement: () => node()}, AbortController, URLSearchParams, Number,
  setTimeout(fn, ms) { const id = Symbol(); timers.set(id, {fn, ms}); return id; }, clearTimeout(id) { timers.delete(id); },
  fetch(url, options) { return new Promise((resolve, reject) => pending.push({url, options, resolve, reject})); },
  L: {circle: (p, o) => layer('circle', p, o), circleMarker: (p, o) => layer('marker', p, o), latLngBounds: points => ({points})}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../satellite_fire.js'), 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
async function resolve(request, value, ok = true) { request.resolve({ok, status: ok ? 200 : 502, json: async () => value}); await flush(); }

const detection = (km, hours, extra = {}) => ({satellite: 'NOAA-21', instrument: 'VIIRS', latitude: 29.5, longitude: -98.5,
  distance_km: km, acquired_at: '2026-11-09T19:17:00+00:00', age_hours: hours, confidence: 'nominal', frp_mw: 5.21,
  daynight: 'day', pixel_scan_km: 0.39, pixel_track_km: 0.36, brightness_ti4_k: 330, version: '2.0NRT', ...extra});
const sources = [{satellite: 'NOAA-20', status: 'ok', url: 'https://x', file_detection_count: 2620, oldest_acquired_at: 'a', newest_acquired_at: 'b'},
  {satellite: 'NOAA-21', status: 'ok', url: 'https://y', file_detection_count: 2212, oldest_acquired_at: 'c', newest_acquired_at: 'd'}];
const data = (detections, extra = {}) => ({status: detections.length ? 'available' : 'not_found', radius_km: 25,
  detection_count: detections.length, returned_count: detections.length, detections, sources, checked_at: '2026-11-10T00:00:00Z',
  nearest_km: detections[0]?.distance_km ?? null, newest_age_hours: detections.length ? Math.min(...detections.map(d => d.age_hours)) : null,
  reason: null, ...extra});

(async () => {
  context.SatelliteFire.load(map, {lat: 29.4242, lng: -98.4933});
  assert.equal(pending[0].url, '/api/v1/satellite-fires?lat=29.4242&lon=-98.4933&radius_km=25&window=24h');
  assert.match(node('satellite-fire-status').textContent, /Checking/);
  assert.match(node('satellite-fire-legend').textContent, /last 24 hours.*red under 6 hours, orange under 12 hours/);
  assert.equal(node('satellite-fire-window').disabled, false);
  await resolve(pending[0], {data: data([detection(3.2, 2, {frp_mw: null}), detection(9.8, 8, {confidence: 'high', daynight: 'night'}), detection(20, 30)], {window: '24h'})});
  assert.match(node('satellite-fire-status').textContent, /3 detections within 25 km of your point in about the last 24 hours.*Nearest 3\.2 km.*2\.0 hours ago/);
  const items = node('satellite-fire-list').children;
  assert.equal(items.length, 3);
  assert.match(items[0].textContent, /3\.2 km · 2\.0 h ago \(2026-11-09 19:17 UTC\) · nominal confidence · FRP not available · NOAA-21 day/);
  assert.match(items[1].textContent, /high confidence · 5\.2 MW · NOAA-21 night/);
  const markers = [...layers].filter(l => l.kind === 'marker');
  assert.deepEqual(markers.map(m => m.options.fillColor), ['#b91c1c', '#ea580c', '#d97706'], 'colour shows age');
  assert.ok([...layers].some(l => l.kind === 'circle' && l.options.radius === 25000), 'search radius ring is drawn');
  assert.match(node('satellite-fire-sources').textContent, /NOAA-20: 2620 US detections.*NOAA-21: 2212/);
  assert.equal(node('satellite-fire-show').hidden, false);
  node('satellite-fire-show').click();
  assert.equal(map.bounds.points.length, 4, 'fit includes the selection and every detection');

  node('satellite-fire-window').value = '7d'; node('satellite-fire-window').change();
  assert.match(pending.at(-1).url, /window=7d/);
  assert.match(node('satellite-fire-legend').textContent, /last 7 days.*red under 1 day, orange under 3 days/);
  await resolve(pending.at(-1), {data: data([detection(5, 10), detection(6, 50), detection(7, 100)], {window: '7d'})});
  assert.deepEqual([...layers].filter(l => l.kind === 'marker').map(m => m.options.fillColor), ['#b91c1c', '#ea580c', '#d97706'],
    '7-day colours use day-scale bands');
  assert.match(node('satellite-fire-status').textContent, /in about the last 7 days/);
  node('satellite-fire-window').value = '48h'; node('satellite-fire-window').change();
  assert.match(node('satellite-fire-legend').textContent, /red under 12 hours, orange under 1 day/);
  node('satellite-fire-window').value = '24h'; node('satellite-fire-window').change();
  node('satellite-fire-radius').value = '50'; node('satellite-fire-radius').change();
  assert.match(pending.at(-1).url, /radius_km=50/);
  assert.equal(layers.size, 0, 'old markers clear as soon as a new lookup starts');
  const many = Array.from({length: 14}, (_, i) => detection(i + 1, 1));
  await resolve(pending.at(-1), {data: data(many, {radius_km: 50})});
  assert.equal(node('satellite-fire-list').children.length, 11, 'ten listed plus a "more" line');
  assert.match(node('satellite-fire-list').children[10].textContent, /4 more on the map/);
  assert.equal([...layers].filter(l => l.kind === 'marker').length, 14, 'every detection is on the map');

  node('satellite-fire-refresh').click();
  const stale = pending.at(-1);
  context.SatelliteFire.load(map, {lat: 30, lng: -97}, true);
  assert.equal(stale.options.signal.aborted, true);
  await resolve(stale, {data: data([detection(1, 1)])});
  assert.equal(node('satellite-fire-list').children.length, 0, 'stale responses are ignored');
  await resolve(pending.at(-1), {data: data([])});
  assert.match(node('satellite-fire-status').textContent, /No satellite fire detections within 25 km of the area center.*does not mean there is no fire/);

  node('satellite-fire-refresh').click();
  await resolve(pending.at(-1), {data: data([detection(4, 1)], {reason: 'Partial: NOAA-20 could not be retrieved; detections from other satellites shown. '})});
  assert.match(node('satellite-fire-status').textContent, /1 detection within.*Partial: NOAA-20/);

  node('satellite-fire-refresh').click();
  await resolve(pending.at(-1), {data: {...data([]), status: 'unavailable', reason: 'These FIRMS files cover the contiguous US and Hawaii'}});
  assert.match(node('satellite-fire-status').textContent, /contiguous US and Hawaii/);

  node('satellite-fire-refresh').click();
  await resolve(pending.at(-1), {error: {message: 'The data provider could not complete this lookup.'}}, false);
  assert.match(node('satellite-fire-status').textContent, /unavailable: The data provider/);
  assert.equal(node('satellite-fire-refresh').disabled, false);

  node('satellite-fire-refresh').click();
  const timed = pending.at(-1);
  [...timers.values()].filter(t => t.ms === 60000).at(-1).fn();
  timed.reject(new Error('aborted')); await flush();
  assert.match(node('satellite-fire-status').textContent, /timed out/);

  context.SatelliteFire.clear();
  assert.equal(node('satellite-fire-refresh').disabled, true);
  assert.equal(node('satellite-fire-window').disabled, true);
  assert.equal(layers.size, 0);
  console.log('Satellite fire panel: request shape, nearest-first list, age colours, FRP/confidence text, radius ring, fit, radius changes, list cap, stale responses, partial, unavailable, errors, timeout and clearing passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
