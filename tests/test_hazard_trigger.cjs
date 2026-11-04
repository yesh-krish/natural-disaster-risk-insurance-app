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
