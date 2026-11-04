const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Exercise the actual map handlers without network or tile dependencies.
const source = fs.readFileSync(require('node:path').join(__dirname, '../script.js'), 'utf8');
const handlers = {};
const elements = {};
function element(id) {
  return elements[id] ||= { hidden: false, value: 'point', textContent: '',
    classList: { add() {}, remove() {} },
    addEventListener(event, fn) { this[event] = fn; } };
}
const layers = new Set();
const selections = [];
let zoom = true;
const context = vm.createContext({
  document: { getElementById: element, addEventListener(event, fn) { handlers[event] = fn; } },
  map: { on(event, fn) { handlers[event] = fn; }, removeLayer(layer) { layers.delete(layer); },
    doubleClickZoom: { enable() { zoom = true; }, disable() { zoom = false; } } },
  L: {
    latLngBounds(a, b) {
      return { getNorth: () => Math.max(a.lat, b.lat), getSouth: () => Math.min(a.lat, b.lat),
        getEast: () => Math.max(a.lng, b.lng), getWest: () => Math.min(a.lng, b.lng),
        getCenter: () => ({ lat: (a.lat + b.lat) / 2, lng: (a.lng + b.lng) / 2 }) };
    },
    rectangle(bounds) { return { bounds, setBounds(b) { this.bounds = b; }, addTo() { layers.add(this); return this; } }; },
  },
  selectLocation(point) { selections.push(point); },
  clearAreaSamples() {},
});
vm.runInContext(`let selectedPoint = null, selectedArea = null, areaLayer = null,
  areaPreview = null, areaStart = null, drawingArea = false, pendingGeocodeRequest;
  const selectionMode = document.getElementById('selection-mode');
  const disasterSelect = document.getElementById('disaster-select');
  const selectionHelp = document.getElementById('selection-help');
  const areaSummary = document.getElementById('area-summary');
  const redrawArea = document.getElementById('redraw-area');
  const cancelArea = document.getElementById('cancel-area');`, context);
vm.runInContext(source.slice(source.indexOf('function finishDrawing()'), source.indexOf('\napplyDisasterSelection();', source.indexOf('function finishDrawing()'))), context);
const mode = elements['selection-mode'];
mode.value = 'area'; mode.change();
assert.equal(zoom, false);
handlers.click({ latlng: { lat: 30, lng: -98 } });
handlers.mousemove({ latlng: { lat: 29, lng: -99 } });
assert.equal(layers.size, 1);
handlers.click({ latlng: { lat: 30, lng: -99 } });
assert.equal(selections.length, 0, 'zero-height rectangles must not submit');
handlers.click({ latlng: { lat: 29, lng: -99 } });
assert.equal(JSON.stringify(selections[0]), JSON.stringify({ lat: 29.5, lng: -98.5 }));
assert.equal(layers.size, 1, 'preview replaced by one final outline');
assert.equal(zoom, true);
assert.match(elements['area-summary'].textContent, /25 samples/);
handlers.click({ latlng: { lat: 31, lng: -97 } });
assert.equal(selections.length, 1, 'completed area remains selected on extra clicks');
elements['redraw-area'].click();
handlers.click({ latlng: { lat: 32, lng: -96 } });
handlers.keydown({ key: 'Escape' });
assert.equal(layers.size, 1, 'cancel preserves previous outline and removes preview');
assert.equal(selections.length, 1);
mode.value = 'point'; mode.change();
assert.equal(layers.size, 0);
assert.equal(elements['area-summary'].hidden, true);
handlers.click({ latlng: { lat: 28, lng: -100 } });
assert.equal(selections.length, 2, 'point mode works after area mode');
elements['disaster-select'].value = 'fireline_intensity';
mode.value = 'area'; mode.change();
assert.match(elements['selection-help'].textContent, /native fireline intensity/);
handlers.click({latlng: {lat: 29.60, lng: -98.62}});
handlers.click({latlng: {lat: 29.64, lng: -98.58}});
assert.equal(elements['export-area'].hidden, true, 'probability CSV hidden for native intensity');
assert.match(elements['area-summary'].textContent, /native fireline-intensity samples/);
console.log('Area selection: bounds, center lookup, degenerate rectangles, redraw/cancel, and point mode passed.');
