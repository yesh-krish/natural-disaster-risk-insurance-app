# NDApp Research API v1

The current contract is **1.13.0**. Version 1.13.0 makes `wildfire` in
`/api/v1/hazards`, `/api/v1/trigger-comparisons` and `/api/v1/areas` use the
nearby reported-fire method, the same one the map uses: each point is a fire
size in acres and the share of record years (FPA FOD, currently 1992–2024) with
at least one reported wildfire of at least that size within `radius_km` (5, 10 or
20; default 10). Trigger comparisons use that observed share of years
(`frequency_basis=historical_annual_frequency`). Sizes never reported within the
radius are left out rather than priced at 0%. `areas/wildfire` uses the area's
center, as the map does. The older wildfire models (`fireline_intensity`,
`potential_fire_intensity` and the FSim flame-length curve) are no longer offered
by the API and return 400; their research notes remain in the docs. Version 1.12.0 adds `window=24h|48h|7d` to
`/api/v1/satellite-fires`, and `hazards/auto` no longer selects the wildfire
flame-length model. Version 1.11.0 added `/api/v1/satellite-fires`:
NASA FIRMS VIIRS active-fire detections near a point (see
[NASA data](nasa-data.md)). Version 1.10.0 lets `/api/v1/monitoring-stations`
take `peril` with `n` to return the nearest n stations in that hazard's network;
the map now lists only those hazard stations (METAR weather readings appear only in
the Heat screening panel). Version 1.9.0 added `/api/v1/assessment`,
which runs the NDApp assessment agent (through its MCP tools, no AI model) and
returns cited facts, per-hazard evidence status, gaps and evidence fingerprints.
Version 1.8.0 added read-only USGS
`/api/v1/elevation-context`, USGS `/api/v1/recent-earthquakes` and FEMA National
Risk Index `/api/v1/risk-index` snapshots, also exposed as MCP tools by
`ndapp_mcp_server.py` (see [risk assessment tools](risk-assessment-tools.md)).
Version 1.7.0 added read-only NWS
`/api/v1/weather-alerts` and FEMA `/api/v1/flood-zone-context` snapshots; see
[risk assessment tools](risk-assessment-tools.md) for semantics and agent handlers.
Version 1.5.0 added `/api/v1/nearest-fire`
([recorded wildfire history](fire-history.md)); version 1.6.0 added
`/api/v1/fire-proximity` for annual reported nearby-fire frequencies, including size-aware exceedance (`size_exceedance`, see [fire-history.md](fire-history.md)).
The [student handoff](student-api-handoff.md) covers the standalone API server,
JavaScript client and remote deployment setup. The map server and standalone
server share the same v1 calculations and response contract.

API **1.4.0** adds `potential_fire_intensity`: historical-weather potential
surface-fire intensity in **kW/m**, a 1991–2020 annual exceedance curve, annual
maxima and source provenance. This is potential-condition frequency on fixed
fuels, not actual wildfire arrival probability. Point mode only; first lookup
may take several minutes. See [method and assumptions](potential-fire-intensity.md).

API **1.3.0** adds nearest-n METAR stations with data links, units and timed
observations. Use `network=awc_metar` and `n=1..50` (default 5), with an address or
coordinates; omit `peril`. Search covers the worldwide NOAA AWC METAR catalog
within 200 km. See [station implementation and limitations](monitoring-stations.md).
Legacy `peril`-only station requests retain their single-station response.

API **1.2.0** added `fireline_intensity`: native Texas A&M Forest Service / TxWRAP
weather-weighted heading intensity in **kW/m**. This remains a legacy research API;
the Texas-only option has been removed from the map. Hazard lookups return `hazard_value`
and provenance with `points: []` and `annual_probability_available: false`.
Rectangles average native intensities across the 25-point grid, retaining all
sample values and provenance. Trigger comparisons explicitly return unavailable
because the source supplies no annual exceedance probabilities. See
[fireline-intensity methods and sources](fireline-intensity.md).

Start the map and API from the project folder:

```powershell
.\.venv\Scripts\python.exe server.py
```

Open [the API explorer](http://127.0.0.1:8000/api/docs). It reads the live
[OpenAPI 3.1 specification](http://127.0.0.1:8000/api/v1/openapi.json), builds
parameter forms, and displays real JSON responses and a curl command. All routes
below use GET. The default base URL is `http://127.0.0.1:8000`.
Use `server.py --port 8001` to run a separate instance on another port.

| Path | Required query parameters | Result |
| --- | --- | --- |
| `/api/v1/health` | None | Local service status; does not check upstream providers |
| `/api/v1/geocode` | `address` | Matched address and approximate coordinates, or explicit match failure |
| `/api/v1/weather-alerts` | `address` OR both `lat`, `lon` | Actual active NWS point-alert products with agency text, timestamps and verified point coverage |
| `/api/v1/flood-zone-context` | `address` OR both `lat`, `lon` | All intersecting FEMA NFHL flood-zone attributes, source units and elevation datum; no polygon means unavailable coverage |
| `/api/v1/assessment` | `address` OR both `lat`, `lon`; optional `hazards` (comma list of `flood`, `earthquake`, `wildfire`; default all) | Full agent evidence screening: facts with evidence IDs and JSON Pointers, `hazard_status`, gaps, evidence sources and SHA-256 fingerprints. `assessment_status` is complete, partial, unavailable, awaiting_input or failed. Typically 10–40 s; needs `requirements-agent.txt` on the server |
| `/api/v1/satellite-fires` | `address` OR both `lat`, `lon`; optional `radius_km` (1–100, default 25), `window` (`24h` default, `48h`, `7d`) | NASA FIRMS VIIRS (NOAA-20, NOAA-21) detections in the chosen rolling US file, nearest first, with time, age, confidence, FRP (MW) and per-satellite status. Thermal anomalies, not confirmed fires; none found is not "no fire"; contiguous US and Hawaii only |
| `/api/v1/elevation-context` | `address` OR both `lat`, `lon` | USGS 3DEP bare-earth elevation in meters (datum not stated by the service); no-data returns unavailable; upstream can take up to 30 s |
| `/api/v1/recent-earthquakes` | `address` OR both `lat`, `lon`; optional `radius_km` (1–300, default 100), `days` (1–3650, default 365), `min_magnitude` (0–9.5, default 2.5) | Full USGS ComCat count plus up to the 100 largest events with epicentral distances; historical catalog, not a probability |
| `/api/v1/risk-index` | `address` OR both `lat`, `lon` | FEMA National Risk Index ratings, scores and expected annual loss for the intersecting census tract; tract-wide relative index, not property risk |
| `/api/v1/hazards/{peril}` | `address` OR both `lat`, `lon`; `radius_km` (5, 10, 20) for wildfire | Source severity–frequency points, units, sources and model details. Wildfire: fire size (acres) vs share of years with such a fire nearby |
| `/api/v1/trigger-comparisons/{peril}` | `payout_usd`, plus `address` OR both `lat`, `lon`; `radius_km` for wildfire | Alternative supported point thresholds and annual contributions |
| `/api/v1/areas/{peril}` | `south`, `west`, `north`, `east`; `radius_km` for wildfire | 25-point grid means and per-threshold/per-sample coverage. Wildfire: the nearby-fire curve at the area's center |
| `/api/v1/monitoring-stations` | `address` OR both `lat`, `lon`; optional `n`, and either `peril` or `network=awc_metar` | `peril` + `n` (1.10.0): nearest n stations in that hazard's own network (USGS stream or precipitation gauges, EarthScope seismic, NIFC RAWS, METAR wind for cyclone). `network=awc_metar` (or neither): nearest n METAR weather stations with observations. `peril` alone: legacy single station |

Hazards are `flood`, `earthquake`, `wildfire` (nearby reported fires by size and radius), `cyclone`, `rainfall`, `heat` and `auto`.

`heat` is historical **annual maximum daily air temperature**, using NOAA
GHCN-Daily TMAX in °C and the 1991–2020 baseline. `frequency_basis` is
`historical_annual_frequency`, with `frequency_pct = 100 × hit_years / complete_years`.
Each included calendar year needs all 365/366 daily TMAX values with blank
quality flags. The nearest qualifying of up to five baseline-spanning US
stations within 100 km is used, with at least 20 complete years. Responses
include `station`, `station_selection`, `baseline`, `annual_maxima`, and source
URL/hash/retrieval provenance. Missing years and rejected nearer stations are
explicit. No current METARs or heat-index bands feed the historical curve.

Heat works through `/api/v1/hazards/heat`,
`/api/v1/trigger-comparisons/heat`, and the map's `/api/disaster?peril=heat`.
Contribution calculations use the observed frequency and are historical
illustrations. They do not establish current/future prices. Heat area requests
return an explicit unavailable result directing clients to Point mode; no shared
station records are averaged over a rectangle. `auto` ranking and the legacy
single-station `peril` route do not include heat. The independent multi-station
METAR route still works for map selections.

The map labels `cyclone` as **Wind (tropical cyclones)**. The API identifier,
units and historical footprint calculation are unchanged; it is not an
all-wind or peak-gust model.

Multi-station METAR responses also include `observation.heat`, an additive
station snapshot with temperature in °C, derived relative humidity (%), shaded
Heat Index in °C/°F, NWS screening band, report timestamp, method and source URLs.
Missing, stale, future-dated or unsupported inputs withhold HI (`null`), never
produce zero hazard. This current snapshot has no annual probability, area mean
or contribution; the separate historical `heat` hazard supplies observed annual
frequencies.
See [heat and wind methods](heat-and-wind.md) and the OpenAPI `HeatScreening`
schema. The legacy single-station metadata route is unchanged.
Hazard, comparison and area routes also accept `auto`, which chooses a supported
hazard using county context. The selection explanation stays in the response.
The station route requires an explicit hazard. Address requests use the US Census
single-address geocoder; coordinates use WGS84 decimal degrees. Hazard/comparison/area routes accept
optional `site_class`: `A`, `B`, `BC` (default), `C`, `CD`, `D`, `DE`, or `E`.
For area requests the server calculates the centroid; `lat` and `lon` are not
accepted. Bounds must be ordered and cannot cross the dateline. Grid size is
fixed at 5×5; 25 provider lookups can take longer than a point lookup.

```bash
curl "http://127.0.0.1:8000/api/v1/hazards/flood?lat=29.39741277&lon=-98.47084089"
curl "http://127.0.0.1:8000/api/v1/trigger-comparisons/flood?lat=29.39741277&lon=-98.47084089&payout_usd=50000"
curl "http://127.0.0.1:8000/api/v1/areas/flood?south=29.39&west=-98.48&north=29.40&east=-98.47"
curl "http://127.0.0.1:8000/api/v1/monitoring-stations?peril=flood&lat=29.39741277&lon=-98.47084089"
curl "http://127.0.0.1:8000/api/v1/trigger-comparisons/flood?address=100%20Dolorosa%20St%2C%20San%20Antonio%2C%20TX&payout_usd=50000"
```

## Street addresses, spaces and typos

For point lookups supply either `address` or both `lat` and `lon`; mixing the two
returns HTTP 400. In the explorer choose **Location input → Street address**.
Use the complete street number, street, city and state, with ZIP code if available.

Leading/trailing whitespace is removed; repeated spaces, tabs and Unicode spaces
collapse to one space; spaces around commas are standardized. Street numbers,
unit identifiers and street spelling are preserved. The limit is 100 characters
after cleanup. Clients should URL-encode the value (`urlencode` in Python does
this automatically); enclose addresses in quotes when using the command line.

The Census geocoder may standardize abbreviations or tolerate some input
differences, but typo correction is not guaranteed. The API does not invent a
spelling correction or choose the closest address. A unique match returns
`meta.location` with `normalized_address`, `matched_address`, approximate
`latitude`/`longitude`, geocoder source and a verification note. `meta.request`
records the resolved coordinates used for the calculation. Check the matched
address and map position, especially after a typo; a unique match is not proof
of the intended property. Changing a street number can match a different valid
address, which the API cannot infer was accidental.

No match returns HTTP 422 (`address_not_found`); multiple distinct matches return
HTTP 422 (`address_ambiguous`) with up to ten candidates in `error.details`.
Neither outcome runs the hazard or station calculation. Correct the input, add
city/state/ZIP details, or use verified coordinates. Provider outages return
HTTP 502. A geocoded point can still have unavailable hazard coverage.

To inspect a match first, call `/api/v1/geocode?address=...`. This lookup itself
returns HTTP 200 with `data.match_status` equal to `matched`, `no_match`, or
`ambiguous`, and top-level `status` of `available` or `unavailable`. It performs
no hazard calculation.

## Response contract

Every v1 lookup includes:

- `api_version`: contract release, currently `1.7.0`.
- `status`: `available`, `unavailable`, `not_found` (stations), or `error`.
- `data`: the provider result, with original source, method, units and notes;
  `null` on errors. Hazard points have `severity` and `frequency_pct`.
- `meta.generated_at`: UTC response generation time, **not** a source-data
  timestamp or model publication date.
- `meta.request`: validated request values, including defaults.
- `meta.location`: geocoder match and approximate location for address requests.
- `meta.purpose`: research-use context.

HTTP 200 means the request completed. Clients must inspect `status` before using
the data. Missing coverage returns `unavailable`; missing depths remain `null`.
Genuine numeric zero frequencies remain zero. Station `not_found` means no
station in that network and radius, not an absence of hazard.

| HTTP status | `error.code` | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Missing, non-finite, blank, duplicate, unknown or invalid parameters |
| 404 | `not_found` | Unknown v1 path |
| 405 | `method_not_allowed` | Unsupported method; use GET |
| 422 | `address_not_found` / `address_ambiguous` | No unique address match; inspect `error.details` |
| 502 | `provider_unavailable` | Provider lookup could not complete; retry later |

Errors include `error.message`, with `data: null`. Parameters are strict so that
misspellings do not silently change an analysis. Existing unversioned map routes
keep their original formats for compatibility.

## Trigger comparisons

`data.comparisons` is sorted by increasing severity. Each row includes:

```json
{
  "severity": 3,
  "frequency_pct": {"minimum": 1, "maximum": 1},
  "expected_annual_contribution_usd": {"minimum": 500, "maximum": 500}
}
```

This row is illustrative, not a measured site result. Equal minimum and maximum
values indicate a single source frequency. Multiple source frequencies at the
same rounded severity retain their range. No unsupported threshold is created,
interpolated or extrapolated. `payout_usd` is required, accepts zero, has at most
two decimal places, and is limited to 1 trillion USD. Contributions are rounded
to cents using half-up rounding.

Contribution = (annual exceedance percentage / 100) × fixed payout. This assumes
at most one fixed payout per year and excludes fees and other pricing adjustments.
Rows are alternative contract choices, not amounts to add together. These
estimates do not model reserves, correlated losses, damage, or solvency.

Cyclone `frequency_basis` is `historical_annual_frequency`, not calibrated future
probability. Wildfire uses `research_estimate`; other supported hazards use
`modeled_annual_probability`. Original provider notes remain in the result.
Zero historical observations do not establish zero future risk.

Area analysis averages available grid-point probabilities at each severity,
using the map's existing interpolation rules. It reports evaluated, missing,
and interpolated sample counts. Counts can differ by threshold. This is not
the probability of an event anywhere in the area. The comparison endpoint is
for individual points; area curve values remain available from the area route.

## Python example

No additional Python packages are needed for the client:

```powershell
.\.venv\Scripts\python.exe examples/api_client.py --peril flood --payout 50000
.\.venv\Scripts\python.exe examples/api_client.py --address "100 Dolorosa St, San Antonio, TX" --peril flood --payout 50000
.\.venv\Scripts\python.exe examples/api_client.py --peril earthquake --site-class D --output comparison.json --csv comparison.csv
```

The [client](../examples/api_client.py) calls the live API, prints a comparison
table and checks availability before using estimates. Optional exports preserve
the full response as JSON and comparison rows as CSV. Use `--base-url` for a
different local port. With unavailable data, the client prints the reason and
does not create a comparison CSV. `--address` replaces coordinate input; `--lat`
and `--lon` must be supplied together and cannot be combined with `--address`.
Omitting all three retains the original San Antonio sample point. The client
prints the matched address and actual coordinates; CSV exports use those resolved
coordinates and include the normalized/matched addresses.

## Operating scope

Both server entry points default to `127.0.0.1` and use the installed raster files
and connected government providers. `server.py` serves the map and API together;
`api_server.py` serves the API alone using FastAPI/Uvicorn, with interactive docs
at `/api/docs`. Install `requirements-api.txt` for the standalone server. Use
`--host` and `--port`, or `HOST` and `PORT`, to configure its listener.

The standalone server supports explicit browser origins through
`NDAPP_ALLOWED_ORIGINS` and limits concurrent lookups to four per application
process. A busy service returns HTTP 503 (`service_busy`) with `Retry-After`;
unexpected adapter failures return HTTP 500 (`internal_error`) without internal
details. Source files and dataset downloads are not served by that entry point.
No public deployment, API-key authentication, per-client request quotas, or billing
is configured. Remote clients need a reachable deployment or their own local copy.

Source coverage and service availability vary. Flood requires installed Texas
tiles; the API does not supply nationwide depth coverage. Provider caching and
timeouts remain those of the underlying map providers. For shared hosting, use
persistent storage for `data/`, HTTPS, and host access controls/request quotas
appropriate to the chosen audience. See the student handoff for Docker commands.
