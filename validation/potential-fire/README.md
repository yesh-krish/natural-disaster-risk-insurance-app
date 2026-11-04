# Potential-intensity integration evidence

Pilot: **30.08, -97.30**, 1991–2020. [Stored computed outputs and provenance](bastrop-1991-2020.json). This demonstrates software/data integration, not scientific validation or observed historical fire intensity.

| Boundary | Evidence |
| --- | --- |
| Public data → model | Five USGS LANDFIRE samples, ERA5 weather and ERA5-Land snow responses; URLs, retrieval times and SHA256 hashes retained. All 30 baseline years passed complete-hour checks. |
| Model → annual curve | 30 annual maxima; 30 supported thresholds. At 107.053507 kW/m, 2006 and 2011 meet the threshold: 2/30 = 6.6667%. Peak states preserve spread, areal heat release, moisture and weather inputs. |
| API → response | Live loopback `/api/v1/hazards/potential_fire_intensity?lat=30.08&lon=-97.3` returned available with 30 years, 30 points and `actual_wildfire_probability_available=false`; cached response took approximately 0.44 s. OpenAPI contract validated. |
| Response → browser | Actual app rendered the curve SVG, 30 annual rows and the potential-condition interpretation. Selecting 107.05 kW/m displayed 6.6667%; an illustrative $10,000 condition-trigger payout displayed $666.67/year with an explicit actual-fire distinction. No captured browser console errors. |
| Automated checks | 55 relevant Python tests passed, including 14 new potential-fire tests. JavaScript trigger and full app DOM-flow checks passed, including the new hazard, annual table, export and stale-state clearing. |

The first server attempt could not run with sandbox socket permissions; an authorized loopback server resolved that issue. No public deployment or paid service was provisioned. The model's academic-use licence is retained in [the documentation](../../docs/licenses/pyretechnics-2026.10.20.txt).

Reproduce with the commands in [the method guide](../../docs/potential-fire-intensity.md). Inputs are cached under ignored `data/potential_fire`; deleting that cache requires fresh provider access. The stored evidence includes calculated results and hashes, not every downloaded hourly observation/reanalysis value. Climate/model uncertainty and limitations are documented in the method guide.

## Loading improvement verification

[The benchmark](loading-benchmark.json) compares sequential and four-process evaluation of the same cached 30-year weather input: **35.984 s → 9.083 s**, with identical annual maxima and curve points. The completed-result disk lookup took **0.023 s**. The benchmark creates no reduced-hour approximation and deletes no caches.

[The live HTTP check](http-loading-check.json) used a fresh public pilot point at 30.08001, -97.3. The first response was **HTTP 202 in 0.074 s**; progress advanced from fuel/terrain lookup through the yearly calculations, and a full 30-year curve was ready after **59.144 s including first-time downloads**. A separate request for the prepared original pilot returned **HTTP 200 in 0.213 s**. These timings distinguish CPU evaluation, network preparation and completed-result serving.

**66 relevant Python tests passed**, including 11 loading/cache/concurrency tests. Full app DOM tests also passed for HTTP-202 polling, completed results, unchanged interpretation and stopping stale polls after a hazard switch. The local server was restarted with the change. Completed year calculations now survive retries, duplicate callers share work, and unrelated code edits do not invalidate scientific caches.

The actual in-app browser was refreshed onto `script.js?v=22` while preserving the user's selected point, trigger and $10,000 payout. The page displayed background preparation, then rendered all 30 annual rows and the curve SVG. Restoring the original trigger displayed the same 86.667% result. Browser control initially needed a session reset; verification completed afterward.
