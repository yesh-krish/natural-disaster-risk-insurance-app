const assert = require("node:assert/strict");
const { hazardTriggerLevels, hazardTriggerBasis, hazardPoolContribution } = require("../hazard_trigger.js");

// The selected level must retain the exact source probability, regardless of
// the frequency order used by the chart or unavailable source scenarios.
for (const peril of ["flood", "earthquake", "rainfall", "wildfire", "cyclone"]) {
  const points = [
    { severity: 4, frequency_pct: 0.2 },
    { severity: null, frequency_pct: 20 },
    { severity: 1, frequency_pct: 10 },
  ];
  assert.deepEqual(hazardTriggerLevels({ status: "available", peril, points }), [
    { severity: 1, minimum: 10, maximum: 10 },
    { severity: 4, minimum: 0.2, maximum: 0.2 },
  ]);
  assert.equal(points[0].severity, 4, "source/chart point order stays unchanged");
}
assert.deepEqual(hazardTriggerLevels({ status: "unavailable", points: [{ severity: 1, frequency_pct: 10 }] }), []);
assert.deepEqual(hazardTriggerLevels(null), []);
assert.deepEqual(hazardTriggerLevels({status: 'available', peril: 'fireline_intensity',
  points: [{severity: 100, frequency_pct: 5}]}), [], 'scenario intensity cannot supply annual triggers');
assert.deepEqual(hazardTriggerLevels({status: 'available', annual_probability_available: false,
  points: [{severity: 100, frequency_pct: 5}]}), [], 'explicit unavailable probability overrides any stray points');
assert.match(hazardTriggerBasis('fireline_intensity').label, /unavailable/);
assert.deepEqual(hazardTriggerLevels({ status: "available", points: [
  { severity: null, frequency_pct: 1 }, { severity: "3", frequency_pct: 1 },
  { severity: NaN, frequency_pct: 1 }, { severity: -1, frequency_pct: 1 },
  { severity: 1, frequency_pct: null }, { severity: 2, frequency_pct: Infinity },
  { severity: 3, frequency_pct: -1 }, { severity: 4, frequency_pct: 101 },
] }), []);
assert.deepEqual(hazardTriggerLevels({ status: "available", points: [
  { severity: 0, frequency_pct: 0 }, { severity: 2, frequency_pct: 100 },
] }), [{ severity: 0, minimum: 0, maximum: 0 }, { severity: 2, minimum: 100, maximum: 100 }]);
assert.deepEqual(hazardTriggerLevels({ status: "available", points: [
  { severity: 1.5, frequency_pct: 1 }, { severity: 1.5, frequency_pct: 10 },
] }), [{ severity: 1.5, minimum: 1, maximum: 10 }]);
assert.match(hazardTriggerBasis("cyclone").label, /Observed/);
assert.match(hazardTriggerBasis("cyclone").note, /not a calibrated future probability/);
assert.match(hazardTriggerBasis("heat").label, /Observed annual heat/);
assert.match(hazardTriggerBasis("heat").note, /1991–2020.*historical station/);
assert.match(hazardTriggerBasis("wildfire").label, /Estimated/);
assert.match(hazardTriggerBasis("earthquake").note, /one year/);
console.log("Trigger levels, source probabilities, missing data, zeros, duplicate levels, and frequency labels passed.");

assert.deepEqual(hazardPoolContribution({ minimum: 1, maximum: 1 }, 50000), { minimum: 500, maximum: 500 });
assert.deepEqual(hazardPoolContribution({ minimum: 0.2, maximum: 0.2 }, 25000), { minimum: 50, maximum: 50 });
assert.deepEqual(hazardPoolContribution({ minimum: 1, maximum: 10 }, 10000), { minimum: 100, maximum: 1000 });
assert.deepEqual(hazardPoolContribution({ minimum: 0, maximum: 0 }, 10000), { minimum: 0, maximum: 0 });
assert.deepEqual(hazardPoolContribution({ minimum: 100, maximum: 100 }, 123.45), { minimum: 123.45, maximum: 123.45 });
assert.deepEqual(hazardPoolContribution({ minimum: 1, maximum: 10 }, 0), { minimum: 0, maximum: 0 });
for (const payout of [null, undefined, "50000", NaN, Infinity, -100]) {
  assert.equal(hazardPoolContribution({ minimum: 1, maximum: 1 }, payout), null);
}
for (const level of [null, {}, { minimum: -1, maximum: 1 }, { minimum: 1, maximum: 101 },
  { minimum: 10, maximum: 1 }, { minimum: NaN, maximum: 1 }]) {
  assert.equal(hazardPoolContribution(level, 50000), null);
}
console.log("Pool contributions: percent conversion, ranges, fractional probabilities, zero payouts/frequencies, and invalid inputs passed.");

const { hazardCustomTriggerLevel, hazardCustomTriggerNote } = require("../hazard_trigger.js");
const close = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-9, `${message}: ${actual} vs ${expected}`);
const curve = [
  { severity: 1, minimum: 10, maximum: 10 },
  { severity: 2, minimum: 1, maximum: 1 },
  { severity: 4, minimum: 0.1, maximum: 0.1 },
];
// Exact source levels are returned unchanged.
assert.equal(hazardCustomTriggerLevel(curve, 2).method, "source");
assert.equal(hazardCustomTriggerLevel(curve, 2).minimum, 1);
// Log-probability interpolation: halfway between 10% and 1% is the geometric mean.
let custom = hazardCustomTriggerLevel(curve, 1.5);
assert.equal(custom.method, "interpolated");
close(custom.minimum, Math.sqrt(10), "geometric midpoint");
assert.deepEqual(custom.basis, [1, 2]);
// Above the range: extend the last segment (1 → 0.1 over 2 units, 10× per 2 units).
custom = hazardCustomTriggerLevel(curve, 6);
assert.equal(custom.method, "extrapolated");
assert.equal(custom.direction, "above");
close(custom.minimum, 0.01, "upper tail");
close(custom.reach, 1, "one segment width past the data");
assert.match(hazardCustomTriggerNote(custom, "m"), /exponential-tail/);
assert.match(hazardCustomTriggerNote(hazardCustomTriggerLevel(curve, 10), "m"), /highly uncertain/);
// Below the range: extend the first segment upward, capped at 100%.
close(hazardCustomTriggerLevel(curve, 0.5).minimum, Math.sqrt(1000), "lower tail");
assert.equal(hazardCustomTriggerLevel(curve, 0).maximum, 100);
// Ranges from duplicate rounded levels carry through each bound separately.
custom = hazardCustomTriggerLevel([{ severity: 1, minimum: 4, maximum: 16 }, { severity: 3, minimum: 1, maximum: 1 }], 2);
close(custom.minimum, 2, "range lower bound");
close(custom.maximum, 4, "range upper bound");
// Zeros: interpolation goes linear, and a zero at the top caps everything above it.
close(hazardCustomTriggerLevel([{ severity: 1, minimum: 2, maximum: 2 }, { severity: 3, minimum: 0, maximum: 0 }], 2).minimum, 1, "linear to zero");
custom = hazardCustomTriggerLevel([{ severity: 1, minimum: 2, maximum: 2 }, { severity: 3, minimum: 0, maximum: 0 }], 5);
assert.equal(custom.bound, "upper");
assert.equal(custom.maximum, 0);
assert.match(hazardCustomTriggerNote(custom, "kn"), /does not establish zero/);
// Flat tails give a bound, not a slope.
custom = hazardCustomTriggerLevel([{ severity: 1, minimum: 5, maximum: 5 }, { severity: 2, minimum: 5, maximum: 5 }], 3);
assert.equal(custom.bound, "upper");
assert.equal(custom.minimum, 5);
// Invalid input.
assert.ok(hazardCustomTriggerLevel(curve, -1).error);
assert.ok(hazardCustomTriggerLevel(curve, NaN).error);
assert.ok(hazardCustomTriggerLevel([], 1).error);
assert.ok(hazardCustomTriggerLevel([curve[0]], 3).error, "one level has no slope");
assert.equal(hazardCustomTriggerLevel([curve[0]], 1).method, "source");
// Observed-record curves (nearby fires) are bounded past the data, never extrapolated.
const fires = [[0.1, 9.0909], [0.3, 6.0606], [1, 6.0606], [3, 3.0303]].map(([severity, pct]) => ({ severity, minimum: pct, maximum: pct }));
assert.ok(hazardCustomTriggerLevel(fires, 100).minimum < 1e-12, "the default exponential tail collapses to ~0");
custom = hazardCustomTriggerLevel(fires, 100, { extrapolate: false });
assert.deepEqual([custom.method, custom.bound, custom.direction, custom.minimum, custom.maximum], ["bounded", "upper", "above", 3.0303, 3.0303]);
assert.match(hazardCustomTriggerNote(custom, "acres"), /highest observed level \(3 acres\).*upper bound, not an estimate.*does not mean zero/);
custom = hazardCustomTriggerLevel(fires, 0.05, { extrapolate: false });
assert.deepEqual([custom.method, custom.bound, custom.minimum], ["bounded", "lower", 9.0909]);
assert.match(hazardCustomTriggerNote(custom, "acres"), /lower bound/);
assert.equal(hazardCustomTriggerLevel(fires, 2, { extrapolate: false }).method, "interpolated", "inside the record still interpolates");
assert.equal(hazardCustomTriggerLevel(fires, 3, { extrapolate: false }).method, "source");
// Contributions accept custom levels.
assert.deepEqual(hazardPoolContribution(hazardCustomTriggerLevel(curve, 2), 50000), { minimum: 500, maximum: 500 });
console.log("Custom triggers: exact, interpolated, extrapolated, observed-record bounds, ranges, zeros, flat tails and invalid input passed.");
