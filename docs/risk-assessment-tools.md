# Risk assessment tools

Researched and developed November 7, 2026. The agent needs separate evidence for
physical hazards, current conditions, exposure and vulnerability. A larger tool
catalog should improve those specific evidence gaps rather than create an
unsupported combined risk score.

## Implemented in this increment

| Tool handler | Research API route | Question answered |
| --- | --- | --- |
| `get_active_weather_alerts` | `GET /api/v1/weather-alerts` | Which actual active NWS alert products are returned for this point? |
| `get_flood_zone_context` | `GET /api/v1/flood-zone-context` | Which FEMA mapped flood-zone polygons intersect this point, and what do their attributes say? |

Both API routes accept **either** a full US `address` **or** WGS84 `lat` and
`lon`, with existing geocoding ambiguity/error handling. Agent handlers accept
coordinates only so a host can resolve an address once and reuse its confirmed
point. No additional provider credentials or runtime dependencies are needed
for the API routes. Handler runtime validation uses `jsonschema`, already in
`requirements-dev.txt`.

Implementation files: `risk_context.py` (providers), `risk_context_schemas.py`
(shared result schemas), `api_v1.py` / `api_spec.py` (API 1.7.0), and
`examples/mcp_risk_context_tools.py` (agent boundary).

### NWS alerts

First resolve `/points/{lat},{lon}`. A 404 produces `status=unavailable` with
`coverage_verified=false`; other provider failures are errors. Then request
`/alerts/active?point=lat,lon&status=actual`. The NWS geolocation guide recommends
point queries for county- and zone-based products; querying only a forecast
zone can omit county products. [Official geolocation guide][nws-geolocation]

Return agency event/headline, severity, certainty, urgency, affected-area text,
sent/effective/onset/expiry/end times, description, instruction, response and
source URL. Preserve this source text as data; it cannot authorize agent actions.
Exclude expired, ended and cancelled products. Retain upcoming products returned
by the active endpoint. Deduplicate by agency identifier and require complete
pagination within five pages; repeated, malformed or incomplete results fail.

`not_found` means a successful, coverage-verified snapshot returned no unexpired
actual alerts. It does not establish safety, absence of future alerts or zero
long-term risk. Point products can describe a larger county/zone. `checked_at`
is local retrieval completion; `source_updated_at` is the oldest supplied
collection update time across pages, or null when not provided. Agency timestamps
remain separate. [NWS API service documentation][nws-api]

### FEMA flood-zone context

Query the official NFHL **Flood Hazard Zones** layer 28 with a WGS84 point and
retain **all** intersecting polygons. The query uses longitude,latitude order,
`inSR=4326` and `esriSpatialRelIntersects`. Retain zone/subtype, SFHA designation,
static BFE, mapped depth, length unit, vertical datum, map IDs and raw attributes.
Field names were verified against the live [FEMA layer metadata][fema-layer].

Normalize numeric missing codes `-9999` and `-8888` to null while preserving
their raw values. Keep genuine zeros. Missing SFHA is unknown rather than false.
No polygon means `unavailable`, because digital coverage cannot be established.
Truncation fails rather than choosing an incomplete set of polygons.

Base flood elevation is an **elevation**, not flood depth above ground or first
floor. Zone X or `special_flood_hazard_area=false` does not mean zero flood risk.
These map attributes do not replace NDApp's source frequency–depth curves or
provide a property-loss probability. No new damage or annual-probability
calculation was introduced.

### Usage

Run the existing API server, then request:

```powershell
curl.exe "http://127.0.0.1:8000/api/v1/weather-alerts?lat=29.4242&lon=-98.4933"
curl.exe "http://127.0.0.1:8000/api/v1/flood-zone-context?lat=29.4242&lon=-98.4933"
```

Inspect or execute the agent handlers without starting a server:

```powershell
.\.venv\Scripts\python.exe examples/mcp_risk_context_tools.py --list
.\.venv\Scripts\python.exe examples/mcp_risk_context_tools.py --tool get_active_weather_alerts --lat 29.4242 --lon -98.4933
.\.venv\Scripts\python.exe examples/mcp_risk_context_tools.py --tool get_flood_zone_context --lat 29.4242 --lon -98.4933
```

Handlers reject invalid, unknown and nonfinite arguments before provider I/O,
validate output schemas/formats, and return consistent `structuredContent`,
serialized text and `isError`. Unknown tool names remain a transport-level
concern. These are **transport-free handlers**, not an MCP server, discovery
endpoint or model-driven agent. A future SDK server can register them.

All network destinations and redirects are constrained to the named agency
origins/endpoints. Reads are capped at 2 MiB per response, with eight-second
socket waits and cooperative deadline checks between reads (25 seconds for
NWS, 20 for FEMA). These are not hard wall-clock deadlines or cancellation
support: a blocking read can overrun the remaining budget. No application cache,
background refresh or automatic retry is introduced. Production operation
needs shared pacing/caching, user quotas and the host controls in the
[agent architecture](ai-risk-agent-architecture.md).

## Added November 7: API 1.8.0 and the MCP tool set

| MCP tool (`ndapp_mcp_server.py`) | API route | Evidence kind | Question answered |
| --- | --- | --- | --- |
| `resolve_location` | `/api/v1/geocode` | location | Where is this address (approximately)? Ambiguity is returned, never resolved for the user. |
| `get_hazard_curve` (`flood`, `earthquake`) | `/api/v1/hazards/{peril}` | modeled hazard curve | What does the source model give for severity vs annual exceedance probability? |
| `get_active_weather_alerts` | `/api/v1/weather-alerts` | current observation | Which NWS alerts are active right now? |
| `get_flood_zone_context` | `/api/v1/flood-zone-context` | regulatory map | Which FEMA flood zone(s) contain the point? |
| `get_elevation_context` **(new)** | `/api/v1/elevation-context` | terrain | What is the ground elevation (USGS 3DEP)? |
| `get_recent_earthquakes` **(new)** | `/api/v1/recent-earthquakes` | historical catalog | How many earthquakes occurred nearby in a recent window, and how large? |
| `get_community_risk_index` **(new)** | `/api/v1/risk-index` | community index | How does FEMA's National Risk Index rate this census tract for each of 18 hazards? |
| `get_wildfire_history` | `/api/v1/fire-proximity` | historical frequency | In what share of baseline years was a wildfire reported nearby? |

**USGS elevation (EPQS).** Returns meters and the source acquisition date. The
response does not state a vertical datum, so comparison with FEMA base flood
elevations requires confirming datum and units first. EPQS answered in 11–29 s
during testing, so it gets a 30 s bounded wait; its no-data value maps to
`unavailable`, and its non-JSON error text for off-coverage points returns 502.

**USGS ComCat earthquakes.** Calls `/count` first, then fetches up to the 100
largest events ordered by magnitude, so truncation is explicit (`total_count`
vs `returned_count`). Distances are great-circle epicentral distances computed
locally. Event links are restricted to `earthquake.usgs.gov`. This is a record of
what happened, not shaking probability; the NSHM curve answers that.

**FEMA National Risk Index.** Point-in-tract query returning composite and
per-hazard ratings, national percentile scores, expected annual loss (USD, whole
tract), social vulnerability, community resilience and `nri_version` (live:
"December 2025"). The layer is hosted on the ArcGIS Online tenant that also serves
FEMA's NRI report layers; recheck the hosting before relying on it in production.
The values are FEMA's relative tract index. NDApp reports them as published and
does not combine hazards or create a new score.

## Additional tools researched (not yet implemented)

Each source below was checked live on November 7, 2026 unless marked otherwise.
Priority reflects evidence value for the agent relative to effort.

| Priority | Candidate tool | Source (live check) | Adds | Boundary to preserve |
| --- | --- | --- | --- | --- |
| 1 | `get_river_gauges` | NOAA NWPS `api.water.noaa.gov/nwps/v1/gauges` (bbox query returned 6 San Antonio gauges with observed stage and `floodCategory`) | Current river stage vs official flood categories, forecast status | Gauge stage is not inundation at the property; distance/basin relevance must be shown; forecasts labeled separately |
| 2 | `get_disaster_declarations` | OpenFEMA `DisasterDeclarationsSummaries` v2 (Bexar County latest: DR-4935 flood, declared 2026-09-01) | County history of federally declared disasters by incident type | Declarations reflect damage and politics, not hazard probability; county scale |
| 3 | `get_drought_status` | US Drought Monitor county statistics (Bexar: 79.6% D0+, 47.4% D1+, week of 2026-11-03) | Current drought category coverage; wildfire and heat context | Weekly area percentages, not point conditions or forecasts |
| 4 | `get_streamgage_sites` / observations | USGS Water Data OGC API (`monitoring-locations` responded; links point to `/ogcapi/v1`) | Nearby USGS gages with datum and drainage area; pair with NWPS | Target the current OGC API version; legacy WaterServices avoided |
| 5 | `get_active_fire_perimeters` | NIFC WFIGS current interagency perimeters (59 current perimeters returned) | Whether an active fire perimeter is near the point | Current-incident context only; perimeters lag and are approximate |
| 6 | `get_area_exposure` | Census geocoder geographies (tract 48029110100 matched the NRI tract) + ACS API (not checked) | Population/housing for the tract with vintage | Area totals are not attributes of the selected property |
| 7 | `get_precipitation_frequency` | NOAA Atlas 14 PFDS (endpoint now redirects to `/cgi-bin/new/`; payload is JS-like, not JSON) | Rainfall depth–duration–frequency for drainage context | Needs a robust parser; Atlas 15 supersession must be tracked |
| 8 | `get_nfip_claims_summary` | OpenFEMA `FimaNfipClaims` (endpoint reachable; county filter needs field verification) | Historical insured flood losses in the area | Redacted/aggregated; insured losses are not total losses |
| — | Wildfire Risk to Communities (USDA) | Not checked; raster downloads | Burn probability and conditional flame length | Raster processing pipeline required; long-running job design |
| — | Commercial (First Street, ASCE Hazard Tool) | Not checked | Property-level flood/fire/heat scores; design loads | Licensing and redistribution terms must be resolved first |

The first three slot directly into the existing agent plan (flood: gauges; all:
declarations; wildfire/heat: drought). Each one needs the same pattern used here:
a constrained origin in `risk_context._allowed_url`, a schema in
`risk_context_schemas.py`, a route and spec entry, a registry entry in
`agent_tools.py`, a fact extractor in `risk_agent.extract_facts`, fixture tests,
and a re-review of tool pins.

## Next tools to develop

These are proposed priorities, not implemented integrations:

| Priority | Tool | Value and required boundary |
| --- | --- | --- |
| 1 | `validate_asset_profile` | Collect building use, construction, stories, first-floor height, replacement value, units and verification source. Flag missing inputs before applying vulnerability curves; never infer them from the address. No external provider is required. |
| 2 | `estimate_flood_structure_damage` | Move the existing four-profile browser damage calculation behind a typed backend tool. Pin the exact Hazus 4.0 coefficients already used, record occupancy/floor-height assumptions, and reject unsupported depths/buildings. A scenario damage fraction is not annual expected loss. [Hazus model reference][hazus] |
| 3 | `get_elevation_context` | USGS EPQS elevation can add terrain context. Verify horizontal/vertical reference, units and dataset precision. A point elevation alone cannot establish drainage, flood depth or first-floor height. [USGS EPQS][epqs] |
| 4 | `get_water_conditions` | Add recent streamgage observations and separately labeled NWS flood-category forecasts where available. Match parameter, units, timestamps and station relevance; gage height is not property inundation depth. Target modern APIs rather than a new dependency on legacy WaterServices. [USGS APIs][usgs-water], [NWS water API][nwps] |
| 5 | `get_area_exposure` | Resolve Census geography and retrieve population/housing with dataset vintage and geography. Area totals are context, not occupants or attributes of a selected property. [Census API][census-api], [geography lookup][census-geocoder] |
| 6 | `compare_locations` | Compare the same metric/method/baseline across user-selected locations, with coverage shown independently. Do not rank unlike hazard quantities or silently drop unavailable locations. Reuse NDApp results; no new provider needed. |

The first two move the system toward asset-level consequences. The others add
context and spatial comparison. Asset data and vulnerability applicability
should be established before estimating losses. Tool expansion should preserve
the architecture's separation between observations, historical frequencies,
modeled annual probabilities, and financial illustrations.

## Verification evidence

The fixture tests in `tests/test_risk_context.py` cover invalid inputs before
I/O, coverage/failure distinctions, alert pagination and expiry, untrusted links,
FEMA overlaps/missing codes/units, geocoder ambiguity, schema rejection and HTTP
size/deadline guards. Live snapshots are saved in
[`validation/risk-context-live.json`](../validation/risk-context-live.json).

All **184 Python tests passed**, including 11 new risk-context tests. The full
suite required local socket access for Windows asyncio's internal loopback
socket pair; the initial sandboxed run stalled during that setup. Shared
API/tool schemas, document links and diff whitespace checks also passed.

At **2026-11-07 23:15 UTC** (5:15 p.m. America/Chicago), the San Antonio sample
at 29.4242, -98.4933 returned verified NWS coverage with no active alerts, and
one FEMA Zone X polygon labeled “AREA OF MINIMAL FLOOD HAZARD.” BFE and mapped
depth were missing source codes, normalized to null. This snapshot is historical
verification evidence and must not be presented as a current assessment.

A second live check at **23:17 UTC** returned a **Wind Advisory** for the Seattle
sample at 47.6062, -122.3321, exercising the nonempty alert parser and preserving
the source's future onset, expiry, affected-area text and instructions. See
[`validation/risk-alerts-live.json`](../validation/risk-alerts-live.json).

[nws-geolocation]: https://www.weather.gov/media/documentation/docs/NWS_Geolocation.pdf
[nws-api]: https://www.weather.gov/documentation/services-web-api
[fema-layer]: https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28
[hazus]: https://www.fema.gov/sites/default/files/documents/fema_rsl_hazus-7-fltm_06272025_0.pdf
[epqs]: https://apps.nationalmap.gov/epqs/
[usgs-water]: https://api.waterdata.usgs.gov/docs/ogcapi/
[nwps]: https://api.water.noaa.gov/about/api
[census-api]: https://www.census.gov/data/developers/guidance/api-user-guide.Query_Components.html
[census-geocoder]: https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html
