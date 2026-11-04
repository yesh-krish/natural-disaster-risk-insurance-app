# Nearest monitoring stations

Implemented and verified November 2, 2026 (America/Chicago; saved evidence uses UTC).

## App and architecture

This is the existing **NDApp natural disaster research map / parametric insurance
pool**: `index.html` and `script.js` use Leaflet; `server.py` serves the static UI
and Python research API; `api_v1.py` dispatches versioned requests. Census address
resolution is shared with hazard lookup in `address_lookup.py`. The existing
hazard providers, installed rasters, area sampling, and payout computations remain
in use. No framework, credentials, database, or package dependency was added.

No `AGENTS.md` was present in the workspace (including nested directories outside
the virtual environment and Git metadata) or any parent directory up to `C:\`.
Checks therefore follow README instructions and the existing test conventions.

## Network selection and scope

The first multi-station network is **NOAA Aviation Weather Center (AWC) METAR**.
It provides weather context, public station metadata, direct station-data links,
and recent observations without requiring an API key. It also reuses the AWC
integration already present for the legacy cyclone single-station lookup.

The eligible set consists of entries in the **worldwide** AWC catalog whose
`siteType` array includes `METAR`, with an ICAO identifier and finite, in-range
WGS84 coordinates, **within 200 km of the selected point**. Rectangular selections
use their existing area center. US street addresses use the existing Census
geocoder; map clicks and coordinate API inputs can be worldwide. This search does
not include all monitoring networks or require stations to be in the same state,
country, or watershed. Catalog membership does not establish active telemetry.

This network is explicitly independent of the selected hazard. METAR observations
are weather context; they do not measure property inundation, seismic motion, or
flame intensity, and do not feed hazard curves, trigger probabilities, or payouts.

## Official sources checked

Verified against the current official documentation and live service:

- [AWC public Data API](https://aviationweather.gov/data/api/): worldwide METAR
  coverage; public HTTPS access; maximum **100 requests/minute**, most API queries
  capped at **400 results**, no browser CORS, custom User-Agent recommended.
  HTTP 204 denotes no data; HTTP 403/429 and server failures must be handled.
  The documented complete station cache is updated daily.
- [AWC OpenAPI YAML](https://aviationweather.gov/data/schema/openapi.yaml):
  `/api/data/metar?ids=KSAT&format=json`, identifiers, observation timestamps and
  native units. No authentication/security scheme is specified; successful live
  requests used no credentials. Its terms-of-service reference is `/data/api/`.
- [Complete station catalog](https://aviationweather.gov/data/cache/stations.cache.json.gz):
  live response was a JSON array with 9,951 entries, including non-METAR sites.
  Filtering on `siteType` is essential. Catalog fields include `icaoId`, `site`,
  `lat`, `lon`, `country`, and `siteType`.
- [NWS disclaimer and permitted use](https://www.weather.gov/disclaimer): NWS
  material is generally public domain unless otherwise noted; lawful reuse must
  preserve attribution, avoid implied endorsement, and not present modified
  material as official. Third-party material can have separate rights. Timely
  delivery is not guaranteed. NWS recommends waiting at least a minute before
  retrying unavailable services.

## User flow and API

Enter a full US street address and **Stations (n)**, then choose **Find address**.
The form accepts integers **1–50** (default 5). The existing geocoder returns the
matched address and approximate coordinates. No match or multiple matches stops
the station search and asks for a refined address or a map selection. Candidates
are never chosen arbitrarily. Invalid geocoder coordinates produce a provider
failure rather than a station query.

The station panel renders an ordered list and one orange map circle per result.
Each list entry contains name/identifier, network, report-variable descriptions,
distance in km, coordinates, latest-report status, and an AWC data link.
**Show selection and stations** fits the selection and all results. Changing n
refreshes stations at the current selection without reloading hazard calculations.
New address submissions clear old station results while resolving the address.
Location/count changes abort earlier requests; request identity and location
version checks prevent late responses from restoring old stations.

```text
GET /api/v1/monitoring-stations?address=100%20Dolorosa%20St%2C%20San%20Antonio%2C%20TX&n=5
GET /api/v1/monitoring-stations?network=awc_metar&lat=29.424206&lon=-98.493261&n=5
```

API version **1.3.0** uses the existing envelope. `data` contains `stations`,
`requested_count`, `returned_count`, `fewer_than_requested`, `network_id`,
`coverage`, `search_radius_km`, and `catalog_fetched_at`. The original
`peril=...` request without `n` or `network` preserves the old single-station
contract; `/api/monitoring-station` also remains available. Combining `peril`
with multi-station inputs is rejected explicitly. See `/api/docs` and the OpenAPI
specification for both forms.

Validation happens before geocoding/provider calls: unknown/blank/duplicate
parameters, invalid n, missing/nonfinite/out-of-range coordinates, unsupported
network, and mixing address with coordinates return **400**. Unmatched or ambiguous
addresses return **422** with match details. Catalog/geocoder failures return
**502**, distinct from **200/not_found** when there are no eligible stations.
Partial results return **200/available**, with actual counts and an explicit
fewer-than-requested flag. Nothing outside 200 km is substituted.

## Geographic correctness and catalog handling

`monitoring_stations.py` downloads the **complete catalog**, filters and
deduplicates by ICAO identifier, computes distances from the exact input, filters
the 200 km circle, sorts by unrounded `(distance_km, id)`, and takes n. Sorting is
local: NOAA does **not** guarantee nearest-first ordering, and an API result limit
must not be mistaken for the nearest eligible set. The full catalog avoids the
400-row truncation risk of bounding-box queries.

Distances use the Haversine great-circle formula with mean Earth radius
6,371.0088 km, including antimeridian/polar geometry, clamping floating-point
roundoff near the antipode. They are straight-line spherical distances, not road
distances or ellipsoidal survey measurements. Only UI text is rounded. Duplicate
identifiers with conflicting metadata use deterministic coordinate/name ordering;
missing or invalid coordinates are skipped and counted. Valid zero coordinates
are not automatically treated as missing.

Station coordinates are accepted from NOAA and not independently verified. During
testing, one catalog entry appeared at (0,0); this illustrates why numerical
coordinate validation cannot establish physical correctness. The no-result live
test uses a Pacific point (0,-140), rather than assuming (0,0) is empty.

## Caching, failures, and observations

All requests go through the Python server because NOAA does not allow CORS.
The complete catalog is cached in memory for **24 hours**. Selected stations'
latest METARs are fetched in one batch of at most 50 IDs and cached **60 seconds**.
Concurrent requests share a lock/cache; outbound requests are spaced at least
one second apart per server process, below the documented 100/minute ceiling.
Failed requests are cached for 60 seconds to prevent retry storms. Multiple server
processes do not share this budget/cache; this is a local research app.

Upstream reads have six-second socket timeouts and response-size caps. The UI
stops waiting after 15 seconds and offers retry. Catalog/schema/decompression
failures remain errors, not empty results; expired catalog data is not silently
served. Observation failures leave every station link and marker usable, with
`observation.status=unavailable`. No automatic polling is added. Retrying within
the failure cooldown can continue to show unavailable until the minute expires.

Report fields currently displayed: air temperature/dew point (°C), wind speed and
gust (kn), wind direction (degrees or variable), altimeter pressure (hPa), and
visibility (statute miles, preserving values such as `10+`). `obsTime` supplies
UTC observation time; response generation and catalog-fetch times are separate.
The newest report per identifier wins even if NOAA returns unsorted duplicates.
Future timestamps more than five minutes ahead and invalid timestamps are ignored.

Reports older than **two hours** are labeled **STALE**. This is an application
threshold, not a NOAA quality certification. No returned report means **missing**;
a report with no supported measured fields is also **missing**. Numeric zero is
preserved. Absent fields remain absent; no estimates fill gaps. Catalog variable
descriptions describe METAR capabilities and explicitly state availability varies
by station/report. Cloud measurements, raw report parsing, quality-flag analysis,
and historical observation browsing are left to the linked NOAA data page.

## Verification and evidence

Live browser and HTTP verification used **100 Dolorosa St, San Antonio, TX**,
matched by Census to **100 DOLOROSA, SAN ANTONIO, TX, 78205**, approximately
**29.424206087348, -98.493261420826**. Five results, in order:

| Station | Name | Distance (km) |
| --- | --- | ---: |
| KSKF | Lackland AFB/Kelly Fld | 9.223 |
| KSSF | San Antonio/Stinson Muni | 9.701 |
| KSAT | San Antonio Intl | 13.383 |
| KRND | Randolph AFB | 25.155 |
| KCVB | Castroville Muni | 35.694 |

All five linked data pages returned HTTP 200; all five had recent reports during
verification. The browser showed matched address, five sorted entries, units,
UTC times, and no warning/error console messages. Requesting 50 returned **44**
with the partial-result notice. Invalid zero cleared markers/list with validation.
Opening the KSKF link in a browser also selected KSKF and displayed its actual
METAR report; NOAA canonicalized the page's `id` parameter to `ids` automatically.
See the [map and station-list screenshot](../validation/monitoring-stations-browser.jpg).

Saved [live evidence](../validation/monitoring-stations-live.json) records the
actual response, timestamps, link checks, invalid counts, missing/nonfinite
coordinates, no stations at (0,-140), request-50 behavior, and an unmatched address.
Reproduce against a running local server:

```powershell
.\.venv\Scripts\python.exe server.py
.\.venv\Scripts\python.exe verify_monitoring_stations.py
```

Deterministic tests cover duplicate stations, unsorted source rows, ties,
antimeridian/polar/radius distances, fewer/zero results, validation-before-network,
ambiguous/malformed geocoding, catalog failure/backoff, latest/stale/missing/failed
reports, native units and zeros, JSON/OpenAPI contracts, and existing API clients.
The full app UI harness covers multiple markers/list entries, fit bounds, links,
safe text rendering, count/location changes, late responses, failures and timeout.

Checks run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_hazard_chart.cjs
node tests/test_hazard_trigger.cjs
node tests/test_area_selection.cjs
node tests/test_area_ui.cjs
node --check script.js
node --check docs/api-explorer.js
.\.venv\Scripts\python.exe -m py_compile monitoring_stations.py api_v1.py api_spec.py
```

All 100 Python tests and all four JavaScript suites passed. Real service failures,
ambiguous addresses, duplicates and stale observations are tested deterministically
rather than forcing failures or assuming the current live catalog contains them.
