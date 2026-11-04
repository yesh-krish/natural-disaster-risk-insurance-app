const assert = require("node:assert/strict");
const {hazardChartSVG} = require("../hazard_chart.js");
const svg = hazardChartSVG([
  {frequency_pct: 20, severity: 1},
  {frequency_pct: 1, severity: 3},
  {frequency_pct: .2, severity: 5},
  {frequency_pct: 10, severity: null},
], "Peak inundation depth (m)", "Annual exceedance probability (%)");
const dots = [...svg.matchAll(/<circle cx="([^"]+)" cy="([^"]+)"/g)].map(m => [Number(m[1]), Number(m[2])]);
assert.equal(dots.length, 3);
assert.ok(dots[0][0] < dots[1][0] && dots[1][0] < dots[2][0], "frequency increases left to right");
assert.ok(dots[0][1] < dots[1][1] && dots[1][1] < dots[2][1], "greater severity is higher on screen");
assert.ok(svg.includes("rotate(-90)"));
assert.ok(svg.includes("Peak inundation depth (m)"));
assert.ok(svg.includes("Annual exceedance probability (%) · log scale"));
const zero = hazardChartSVG([{frequency_pct: 0, severity: 34}, {frequency_pct: 0, severity: 64}], "Wind (kn)", "Observed annual frequency (%)");
assert.ok(zero.includes("linear scale"));
assert.ok(!zero.includes("NaN") && !zero.includes("Infinity"));
assert.equal(hazardChartSVG([{frequency_pct: 1, severity: null}], "m", "%"), null);
assert.ok(hazardChartSVG([{frequency_pct: 1, severity: 0}], "<unsafe>", "%").includes("&lt;unsafe&gt;"));
console.log("Hazard chart axis, missing-value, and zero-frequency checks passed.");
