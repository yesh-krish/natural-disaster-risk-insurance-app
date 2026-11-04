# Heat screening and Wind terminology

Initial research: **November 2, 2026**. Historical selectable-hazard update:
**November 3, 2026 (America/Chicago)**.
This document records the scientific decision, compatibility boundary and checks
for the existing local NDApp. It is not a workplace heat-exposure standard.

## Current Heat selection: historical annual exceedance

Following the user's clarification, **Heat (historical annual exceedance)** is
now a selectable hazard. The main Heat view uses NOAA GHCN-Daily historical
daily maximum air temperature, not the current METAR snapshot. The former
snapshot panel is collapsed optional context visible only under Heat.

The event definition is **at least one source observing-day TMAX at or above
the selected temperature during a calendar year**. The annual statistic is
`M_y = max(valid daily TMAX in year y)`, in °C. Its observed exceedance frequency
at a supplied threshold `x` is `100 × count(M_y ≥ x) / count(included years)`.
The time horizon is one historical calendar year, with a fixed **1991–2020**
baseline. This baseline matches the period of the current [NOAA U.S. Climate
Normals](https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals),
but the app computes its own empirical annual-maxima frequencies from daily
records; it does not present them as official NOAA normals or future AEPs.

Air temperature is appropriate here because GHCN-Daily supplies daily TMAX.
Its [primary documentation](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt)
defines TMAX in tenths of °C, `-9999` as missing and blank quality flags as values
that did not fail QA. Historical HI would require temperature and humidity at
coincident times; daily maxima and unrelated humidity averages cannot establish
a daily maximum HI. WBGT requires the separate environmental/workplace inputs
described below. No Heat Index caution band is reused as an air-temperature
hazard score or construction limit.

The implementation in `historical_heat.py` uses these declared coverage rules:

- Read NOAA's station metadata and TMAX inventory. Consider US station IDs with
  first/last inventory years spanning 1991–2020. Inventory span does not imply
  continuous observations.
- Within **100 km**, check up to the first **five** candidate stations in
  great-circle-distance order, using station ID to break distance ties. Select
  the first with at least **20 complete years**. Disclose every checked station,
  its distance, complete-year count and source provenance. If all checked records
  fail coverage, leave the curve unavailable. A provider failure stops the lookup
  instead of being disguised as missing climatic coverage.
- Require every real day of each included year: 365 or 366 TMAX values, with
  blank quality flags and no missing sentinel. Missing/flagged days exclude the
  whole year and its maximum from the sample; no interpolation or imputation.
  Dates outside the real month length are padding, not days. Malformed, truncated,
  duplicate or mismatched station records fail closed.
- The 100 km, five-candidate and 20-complete-year requirements are **app coverage
  policies**, not NOAA exposure standards or physiological thresholds. A station
  farther away can qualify when a nearer record has gaps; its distance and the
  exclusion are visible. Frequencies describe included years, so selective
  missingness can bias the sample.

Only unique observed annual maxima become selectable thresholds. Repeated hot
days or months do not inflate the count of years. Equality counts as exceedance.
There are no estimated tails above the observed maximum, invented return periods
or zero-risk claims for unobserved extremes. With `n` included years, observed
frequency resolution is `100/n` percentage points; a few decades do not support
precise very rare-event estimates. Connecting chart lines guide the eye; the
underlying empirical distribution is a step function and trigger values are not
interpolated or extrapolated.

Daily dates remain source observing-day dates, not invented UTC instants.
Station observing-day boundaries, changes in measurement practice/location and
urban/elevation differences can affect interpretation. NOAA's measurement flags
are retained for each annual maximum; for example `H` can mean the highest
hourly temperature rather than a continuously sampled maximum, and `L` marks
lagged temperatures. Blank QA is not proof of perfect measurement. For tied
maxima, the latest source date is reported without changing the annual value.
The baseline is not detrended or adjusted for warming after 2020.

The main view displays station name/ID/coordinates/distance, baseline and usable
years. Its expandable annual table and CSV preserve included/excluded years,
valid-day denominators, maxima in °C/°F, dates, threshold frequencies and source
URL/SHA256/retrieval time. The API also retains all catalog/daily-source hashes
and maximum measurement/source flags. Source text is cached in memory by UTC
day (one parsed catalog and up to 16 station-year records); no database or new
dependency is needed. A cold lookup downloads about 36 MB of inventory plus
station metadata and the necessary station records, with bounded sizes/timeouts.
The source service can revise historical records. Hashes document the retrieved
version but do not themselves archive the underlying raw file.

The existing trigger/comparison/contribution flow now accepts `peril=heat`.
Probability wording changes to **observed annual heat exceedance frequency**.
Any frequency-times-payout result is explicitly a historical-frequency
illustration, not a forward-looking price. `cyclone` keys/formulas and all other
hazard computations are preserved. Heat does not enter automatic county hazard
ranking or the separate research-engine bundle/schema.

This first historical release supports **Point mode** using a station proxy.
Area requests explicitly return unavailable rather than averaging repeated
copies of one station record over a grid. A one-day annual maximum does not
establish heat-wave duration, warm-night exposure, a worker's heat dose or a
shutdown limit. Those remain separate product/scientific decisions.

## Inspection before editing

No `AGENTS.md` was present in the workspace, its subdirectories (excluding the
virtual environment/data/Git internals), or its ancestor directories through
`C:\`. No repository-specific instructions could therefore be read.

The inspected application consists of static HTML/JavaScript and a local Python
HTTP server. Its viewer has flood, earthquake, wildfire, tropical-cyclone wind
and rainfall curves. The Texas-only fireline-intensity map option has since been
removed; its legacy research API remains available. The
separate research engine/bundle is not the same interface as the viewer.

| Existing viewer quantity | Source and transformation | Interpretation retained |
| --- | --- | --- |
| Flood depth (m) | Installed TWDB scenario-5 rasters, feet converted to meters | Four modeled annual-chance scenarios; missing/ambiguous zero cells stay missing |
| Earthquake PGA (%g) | USGS NSHM; `1 − exp(−annual rate)` | Modeled annual exceedance with explicit site class |
| Wildfire flame length (m) | USFS FSim burn probability × conditional flame-length probability | Research annual exceedance estimate |
| Legacy fireline intensity API (kW/m) | Texas A&M Forest Service/TxWRAP native statistic | Removed from map; no annual probability |
| Wind, tropical cyclones (kn) | NOAA NHC HURDAT2 quadrant wind radii at 34/50/64 kn | Share of complete years since 2004 with a sampled footprint |
| Rainfall depth (mm) | NOAA Atlas 14 annual maximum, 24-hour depth | Annual exceedance `1 / return period` |

`script.js`, `hazard_trigger.js`, `area_hazard.py`, `api_v1.py` and their tests
were inspected for point/rectangle flows, annual contributions, CSVs and stale
response handling. Annual contributions still use annual probability/frequency
times the selected fixed payout. Rectangle curves still average compatible
probabilities across the existing 5×5 grid. Neither consumes heat observations.

Runtime storage is installed TWDB/USFS files and process-local source caches.
There is no runtime database or browser local/session storage in the map script.
`database/hazard_schema.sql` is an unapplied PostGIS blueprint; the research JSON
schema uses its own existing peril/metric definitions. No migration, database
edit, metric-identifier change or stored-value rewrite is needed for this work.
The workspace files were already untracked at inspection; unrelated files were
retained.

## Wind naming and actual model scope

The visible category is **Wind (tropical cyclones)**; shorter chart/map labels
use **Wind**. The automatic selection name also discloses the tropical-cyclone
scope. The HTML option value, API peril `cyclone`, Python functions, response
peril/metric identifiers, probability basis, units and existing formulas remain
compatible. API documentation explains the retained key.

This is a presentation change, not a general wind-hazard model. The parser uses
Atlantic and northeast/central Pacific HURDAT2 records with TS/HU/SS status and
wind-radius data from 2004 through the latest common complete calendar year in
the downloaded basin files. The viewer's existing coordinate guard is 0–65°N,
180°W–0°. NHC documents quadrant radii and predominantly six-hourly best-track
times; NHC sustained wind uses a one-minute convention. See the [NHC data
archive](https://www.nhc.noaa.gov/data/), [HURDAT2 format
documentation](https://www.nhc.noaa.gov/data/hurdat/hurdat2-format-atl-1851-2021.pdf)
and [NOAA sustained-wind explanation](https://www.aoml.noaa.gov/hrd/tcfaq/D4.html).

The app counts years rather than repeated records or storms in the same year.
It does not interpolate the storm trajectory between records, reconstruct
missing radii, model tornadoes/thunderstorms/other windstorms, or infer local
peak gusts. Historical footprint frequencies are not calibrated future AEPs,
building-code design speeds or a statement of zero future hazard when no hits
are recorded. FEMA automatic ranking still uses hurricane expected annual loss
(`HRCN_EALT`); relabeling it does not broaden that source's scope.

## Three different heat questions

| Question | Required evidence and horizon | Implemented? |
| --- | --- | --- |
| What does a nearby station's latest report indicate? | Coincident temperature/dew point, station identity/location and observation time; one report | Yes: temperature and derived shaded Heat Index snapshot |
| What heat is expected in coming hours/days? | Forecast provider, issue time, valid times and forecast uncertainty | No. A current report is never relabeled as a forecast |
| How often is an annual daily-temperature threshold reached? | Historical GHCN-Daily TMAX, complete-year coverage, 1991–2020 baseline and station geography | Yes: observed annual exceedance; heat-wave duration and overnight exposure remain separate |

For a future US heat-wave planning view, the [NWS HeatRisk
product](https://www.wpc.ncep.noaa.gov/heatrisk/) is a relevant *separate* forecast
source: it considers local climatology, duration and overnight relief over the
coming seven days. Its published categories must be retrieved with their valid
dates; they must not be recreated from one temperature/HI reading. Forecast
probabilities would also be distinct from historical annual exceedance.

## Metric comparison and recommendation

| Metric | Inputs and units | Appropriate use | Material limits |
| --- | --- | --- | --- |
| Air temperature | Dry-bulb thermometer, °C or °F | Direct weather context; with historical daily maxima/minima, a possible basis for heat-wave climatology | Does not represent humidity, solar/radiant exposure, clothing or metabolic workload; one observation cannot establish a heat wave |
| Heat Index | Simultaneous air temperature plus relative humidity; apparent temperature, °F internally and °C/°F displayed | The smallest useful **station-based screening** feature supported by this app's existing reports | Shade/light-wind assumptions; not measured worksite exposure or WBGT; misses radiant heat and workload; empirical approximation with range/precision limits |
| WBGT | On-site natural wet-bulb, black-globe and dry-bulb measurements, or a separately validated meteorological model with radiation/wind and other necessary inputs; °C/°F of WBGT | Preferred environmental metric for **outdoor construction assessment**, evaluated with workload, clothing/PPE, acclimatization and exposure/rest pattern | Station temperature/dew point alone do not supply natural wet-bulb or globe temperature. Wind in METAR does not resolve solar/radiant heat at a worksite |

The NWS defines HI from temperature/humidity under shaded conditions. OSHA
recommends on-site WBGT, explains the limitations of station HI for workplaces,
and distinguishes environmental heat from metabolic heat. These support
**air temperature for historical annual exceedance, HI for optional current
screening and WBGT for a later worksite module**.
See [NWS Heat Index](https://www.weather.gov/ama/heatindex) and [OSHA Heat Hazard
Recognition](https://www.osha.gov/heat-exposure/hazards). No fixed solar increment
is added: an “up to” sunlight effect is not a universal correction.

Occupational limits are conditional. The [NIOSH criteria and current clothing
guidance](https://www.cdc.gov/niosh/docs/2016-106/) distinguish acclimatized and
unacclimatized workers, metabolic load and clothing adjustments. Their
recommendations cannot be reduced to a universal construction shutdown
temperature. This implementation supplies no work/rest schedule, legal
compliance determination, work authorization or stop-work instruction.

## Implemented data and calculation contract

**Source and coverage.** The existing
`GET /api/v1/monitoring-stations?network=awc_metar&lat=…&lon=…&n=…` route is reused.
Its worldwide NOAA AWC catalog entries must be marked METAR, have usable
coordinates and lie within 200 km of the selected point or rectangle center.
Results remain distance-sorted; missing reports do not cause a farther station
to be silently substituted. Street-address lookup is US-only. Airports can
be far from and unrepresentative of a construction site. Values are displayed
per station, never as a property reading or area mean. [AWC's API
documentation](https://aviationweather.gov/data/api/) identifies METAR as
worldwide terminal observations, distinct from TAF forecasts.

**Time and caching.** `obsTime` is the report's Unix time in seconds. The existing
adapter selects the newest acceptable report for each ID and emits an ISO UTC
timestamp. Heat requires an offset-aware timestamp no later than evaluation
time and no older than 7,200 seconds. Exactly two hours is accepted; older
reports are withheld. Two hours is the app's existing freshness policy, not a
scientific safe-exposure duration. Cached reports are rechecked on each lookup.
AWC catalog caching remains 24 hours, report caching 60 seconds and failure
backoff 60 seconds, with existing request pacing. Response generation and
catalog-fetch times do not replace observation time.

The browser explicitly labels a **snapshot**, shows report time and age at
lookup, and offers **Refresh heat and station reports**. It does not automatically
poll or keep an aging snapshot current. Changing location, count, address or
hazard clears the previous heat results; obsolete responses are ignored. Errors,
timeouts, no eligible stations and an older backend lacking heat fields have
explicit unavailable messages.

**Units and humidity.** Temperature and dew point must come from the same
normalized METAR report in °C. Missing, string, boolean, nonfinite or grossly
out-of-bounds values are not converted into estimates. The input sanity envelope
is −80 to 60 °C and dew point must not exceed air temperature; it is an
implementation validation guard, not a risk threshold. Valid zero temperatures
are retained. Relative humidity is an estimate over water, not a reported
humidity sensor measurement, using the [NWS vapor-pressure
equations](https://www.weather.gov/media/epz/wxcalc/vaporPressure.pdf):

```text
e(Td) = 6.11 × 10^(7.5 Td / (237.3 + Td))  [hPa; Td in °C]
es(T) = 6.11 × 10^(7.5 T / (237.3 + T))    [hPa; T in °C]
RH = 100 × e(Td) / es(T)                  [%]
T_F = 1.8 × T_C + 32
```

**Heat Index method.** `heat_context.py` follows the [WPC Heat Index
algorithm](https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml): simple
formula and averaging with air temperature for the initial screen, Rothfusz
regression when that screen reaches 80 °F, with its specified low-humidity and
high-humidity adjustments. HI °C = `(HI °F − 32) × 5/9`. Raw values are retained
in the API; display uses one decimal. Classification uses the unrounded value.
One decimal is display formatting, not a claim of sensor or model accuracy;
METAR rounding, derived humidity and the empirical fit add uncertainty. Source
sensor calibration/quality is not independently verified by this feature.
Published NWS examples at 100 °F / 55% RH (about 124 °F HI) and 100 °F / 15% RH
(about 96 °F HI) are regression checks.

To bound this screening feature, numeric HI is withheld outside air temperature
76–126 °F, RH 4–100%, or calculated HI above 183 °F. These are **product display
guards based on the extent of NOAA's [expanded HI
chart](https://www.noaa.gov/sites/default/files/2022-05/heatindex_chart_rh.pdf)**,
not physiological limits or a claim that every combination inside the envelope
is independently validated. The empirical equation remains uncertain at extreme
combinations. Withholding a value does not mean less hazard. Cool/dry conditions
can retain air temperature while lacking a displayed HI; no safe label is used.

**Bands.** The app deliberately uses the named public screening bands from
[NWS Amarillo](https://www.weather.gov/ama/heatindex), not a newly invented score:

| Unrounded Heat Index (°F) | Displayed interpretation |
| --- | --- |
| Below 80 | Below listed caution bands; heat illness is still possible |
| 80 ≤ HI < 90 | Caution |
| 90 ≤ HI < 103 | Extreme caution |
| 103 ≤ HI < 125 | Danger |
| HI ≥ 125 | Extreme danger |

Half-open intervals resolve the source table's shared 90/103 endpoints and its
integer “103–124 / 125 or higher” presentation. These are neither local advisory
issuance rules nor occupational exposure limits. Other NOAA educational charts
use different bands (including the expanded chart's 105/130 boundaries); this
app does not mix their classifications. The expanded chart supplies only a
display envelope. No score, annual frequency, shutdown threshold or individual
heat-illness probability is derived from these category names.

**API and storage.** Each multi-station `observation` gains an optional additive
`heat` object, documented as `HeatScreening` in OpenAPI. It contains status,
`data_kind: observation_derived`, metric, coincident input values/units in field
names, derived RH, HI in °C/°F, category, report time, method, reasons and primary
source URLs. Unavailable derived values are `null`, not zero. Raw station
observations and links remain usable during heat-data failure. This current
observation object does not enter annual curves or contribution calculations.
The separate historical `heat` peril above uses GHCN-Daily records for curves
and comparisons. Research pricing, database schema and auto-ranking are
unchanged. The historical provider uses standard-library networking/parsing.

## Specific blockers for broader heat features

1. **Multi-day heat-wave hazard:** historical annual daily-temperature
   exceedance is now implemented as described above. A duration-specific heat-wave
   event still needs decisions about consecutive days, absolute/local percentile
   thresholds and treatment of warm nights, with a compatible time series. No
   duration or overnight metric is inferred from annual maxima.
2. **Construction exposure:** there is no on-site natural wet-bulb/globe data,
   validated local WBGT model, workload, clothing/PPE, acclimatization or work/rest
   pattern. Those inputs and the applicable guidance are prerequisites for an
   occupational assessment. Computing “WBGT” from temperature/dew point alone or
   issuing one shutdown limit would be unsupported. No dependent calculation was
   implemented.
3. **Forecast:** no forecast source is currently wired into the heat view.
   A later addition must preserve forecast issue/valid times and attribution;
   integrating official HeatRisk is preferable to inventing an equivalent score.

These blockers do not prevent observation screening; the UI makes that limited
purpose explicit.

## Verification

**November 3 historical Heat update:** 122 Python tests and all four JavaScript
entry points passed, including 10 new historical-data tests and the full-script
Heat selection/trigger/CSV harness. Checks cover tenths-°C conversion, leap days,
valid zeros, missing sentinel, QA exclusion, malformed/duplicate records,
inclusive annual hit counts, repeated daily readings, complete-year
denominators, minimum coverage, nearer-station rejection, source failures,
OpenAPI, historical contribution labels and point-only area handling. Switching
hazards/locations clears stale historical results and disables the export.

`verify_historical_heat_api.py --base-url http://127.0.0.1:8767` passed live point,
legacy viewer, trigger-comparison, schema, uncovered-location, invalid-input and
unsupported-area checks. [Saved evidence](../validation/historical-heat-live.json)
records San Antonio International Airport **USW00012921**, **13.39 km** from the
test point, **30 complete years** and **12 observed thresholds**. The closer
Kelly AFB station **USW00012909** was explicitly excluded with zero complete
baseline years. The daily source, inventory and metadata each have a retrieval
timestamp and SHA256. NCEI documentation and the baseline source were reviewed
directly on November 3. No new visual browser pass is claimed; the browser
automation limitation recorded below remains applicable.

The same live check also passed on the normal app URL,
`http://127.0.0.1:8000`, after replacing the verified stale NDApp process with the
updated local server. The updated server was left running for the user; refresh
the browser to load the new selector and scripts.

The following records describe verification of the original November 2
current-snapshot feature.

Required repository checks passed: **107 Python tests**, all four JavaScript
test entry points, and `node --check script.js`. New tests cover published NWS
examples, adjustments, °C/°F conversion, derived humidity, unrounded category
boundaries, missing/wrong-unit/boolean/nonfinite values, valid zeros, inconsistent
dew point, chart display guards, timestamp offsets, future/stale reports and
the exact two-hour boundary. API checks validate the additive response against
OpenAPI with date-time format checking and finite JSON serialization.

The complete application-script UI harness checks per-station heat text/units,
distances, timestamps, stale/absent data, old backend handling, inert provider
text, no-results/error/timeout states, refresh and selection changes. Existing
curve/trigger/contribution, spatial averaging, source-specific units, real zeros
and missing-coverage checks still pass. Wind regression checks retain the
`cyclone` identifier, wind-radius geometry and year-count calculation.

`verify_heat_api.py --base-url http://127.0.0.1:8767` passed a live HTTP check
against the updated server. The saved [live response and edge-case
evidence](../validation/heat-stations-live.json) contains the nearest San Antonio
stations KSKF/KSSF/KSAT with available, observation-derived heat snapshots,
original UTC report times and units. The check validates OpenAPI including
date-time formats, Celsius/Fahrenheit consistency, report identity, explicit
no-station coverage at 0°N/140°W and HTTP 400 for invalid coordinates/counts.
It does not assume that live weather must be hot or must yield available HI.

Visual browser verification could not be completed: the in-app browser timed
out/failed to attach to the local site, and native-browser accessibility capture
also failed. The sandbox-isolated server was inaccessible over HTTP; running
the test server outside that isolation allowed the live API checks above.
No visual screenshot or browser-console pass is claimed. Rendering and selection
behavior were verified with the complete application-script DOM/map/API harness.

## Primary source access notes

Primary sources above were reviewed on November 2, 2026 (local date). NWS/NOAA,
AWC, OSHA and CDC/NIOSH were used; no third-party calculator, inferred score or
universal occupational threshold was adopted. WPC's equation URL returned 403
on direct browser-tool fetches; indexed WPC algorithm passages were available,
and the regression/example values were cross-checked against the directly
accessible NWS Amarillo page. This software verification does not constitute
observational calibration or occupational validation.
