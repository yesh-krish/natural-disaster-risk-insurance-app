const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const nodes = new Map(), pending = [], timers = new Map(), layers = new Set(), events = {};
function node(id = '') {
  if (nodes.has(id)) return nodes.get(id);
  const element = { children: [], hidden: false, disabled: false, textContent: '',
    append(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
    addEventListener(name, callback) { this[name] = callback; } };
  if (id) nodes.set(id, element);
  return element;
}
const map = { removeLayer(layer) { layers.delete(layer); }, fitBounds(bounds) { this.bounds = bounds; } };
function layer(point) { return { point, addTo() { layers.add(this); return this; }, bindPopup(popup) { this.popup = popup; return this; } }; }
const document = { hidden: false, getElementById: node, createElement: () => node(),
  addEventListener(name, callback) { events[name] = callback; } };
const context = vm.createContext({ document, AbortController, URL, URLSearchParams,
  setTimeout(fn, ms) { const id = Symbol(); timers.set(id, {fn, ms}); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(url, options) { return new Promise((resolve, reject) => pending.push({url, options, resolve, reject})); },
  L: { circleMarker: layer, polyline: layer, latLngBounds: points => points },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../fire_history.js'), 'utf8'), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const payload = { data: { status: 'available', time_window: 'all_available_history',
  fire: { name: '<img src=x onerror=alert(1)>', latitude: 29.4, longitude: -98.5,
    distance_km: 3.256, discovery_date: '2011-08-03', reported_acres: 1,
    source_name: 'USFS', date_note: 'Discovery date.', record_url: 'https://apps.fs.usda.gov/fire/query?f=pjson' },
  sources: [{coverage: 'Archive 1992–2024'}, {coverage: 'Ongoing incidents'}],
  checked_at: '2026-11-05T00:00:00Z', message: 'Reported point; property burn extent unknown.' } };
const resolve = async (request, value = payload, ok = true) => {
  request.resolve({ok, json: async () => value}); await flush();
};

(async () => {
  context.FireHistory.load(map, {lat: 29.4241, lng: -98.4936});
  assert.match(pending[0].url, /^\/api\/v1\/nearest-fire\?lat=29.4241&lon=-98.4936$/);
  await resolve(pending[0]);
  assert.match(node('nearest-fire-status').textContent, /3.26 km.*2011-08-03/);
  assert.match(node('nearest-fire-status').textContent, /<img/); // rendered as inert text
  assert.equal(layers.size, 2);
  assert.equal(node('nearest-fire-show').hidden, false);
  node('nearest-fire-show').click();
  assert.equal(map.bounds.length, 2);
  assert.equal(map.bounds[1].lat, 29.4);
  assert.match(node('nearest-fire-details').children.map(n => n.textContent).join(' '), /All available history; no date filter/);

  // New selection clears old markers immediately and ignores an aborted response.
  node('nearest-fire-refresh').click();
  const stale = pending.at(-1);
  context.FireHistory.load(map, {lat: 30, lng: -99}, true);
  assert.equal(layers.size, 0);
  assert.equal(stale.options.signal.aborted, true);
  const current = pending.at(-1);
  await resolve(current, {data: {...payload.data, fire: {...payload.data.fire, record_url: 'javascript:alert(1)'}}});
  assert.match(node('nearest-fire-status').textContent, /area center/);
  assert.equal(node('nearest-fire-details').children.some(n => n.href), false);
  await resolve(stale);
  assert.match(node('nearest-fire-status').textContent, /area center/);

  // Periodic refresh, provider failures, timeout, empty records, and manual retry.
  [...timers.values()].find(timer => timer.ms === 300000).fn();
  assert.equal(layers.size, 0);
  await resolve(pending.at(-1), {error: {message: 'Provider offline'}}, false);
  assert.match(node('nearest-fire-status').textContent, /unavailable.*Provider offline/);
  assert.equal(node('nearest-fire-refresh').disabled, false);
  node('nearest-fire-refresh').click();
  await resolve(pending.at(-1), {data: {...payload.data, status: 'not_found', fire: null}});
  assert.match(node('nearest-fire-status').textContent, /No eligible dated wildfire/);
  assert.equal(layers.size, 0);
  document.hidden = true;
  const beforeHidden = pending.length;
  [...timers.values()].filter(timer => timer.ms === 300000).at(-1).fn();
  assert.equal(pending.length, beforeHidden);
  document.hidden = false;
  events.visibilitychange();
  assert.equal(pending.length, beforeHidden + 1);
  const timed = pending.at(-1);
  [...timers.values()].filter(timer => timer.ms === 55000).at(-1).fn();
  timed.reject(new Error('Aborted')); await flush();
  assert.equal(timed.options.signal.aborted, true);
  assert.match(node('nearest-fire-status').textContent, /timed out/);
  context.FireHistory.clear('Waiting for address match');
  assert.equal(node('nearest-fire-status').textContent, 'Waiting for address match');
  assert.equal(node('nearest-fire-refresh').disabled, true);
  assert.equal(layers.size, 0);
  events.visibilitychange();
  assert.equal(pending.length, beforeHidden + 1);
  console.log('Nearest fire UI: all-history API, distance/date, map fit, safe text/links, stale response isolation, 5-minute refresh, failure, empty results and clearing passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
