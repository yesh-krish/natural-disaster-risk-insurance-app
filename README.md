# NDApp natural disaster research map

**AI risk assessment agent over MCP:** `ndapp_mcp_server.py` is a real MCP
server (official Python SDK, stdio) exposing eight reviewed, read-only evidence
tools. `risk_agent.py` is the host: it plans a bounded set of MCP calls, pins
tool definitions, records hashed evidence, extracts traceable facts and can ask
Claude to explain them under code-enforced citation checks. Install
`requirements-agent.txt`, then run
`python risk_agent.py --lat 29.4242 --lon -98.4933 --hazards flood earthquake`.
See the [agent architecture](docs/ai-risk-agent-architecture.md).
The same agent is also available over HTTP at `/api/v1/assessment` (no map panel
yet); the server needs `requirements-agent.txt` installed.

**Risk assessment tools:** the API now includes active NWS point alerts and FEMA
flood-zone context, with validated agent-tool handler prototypes. These add
current and regulatory context alongside the existing hazard curves. See
[implemented tools, usage and the next development priorities](docs/risk-assessment-tools.md).

**Using NDApp from another project:** the standalone JSON API runs independently
of the map. Install `requirements-api.txt`, run `python api_server.py`, and open
`http://127.0.0.1:8000/api/docs`. See the
[student API handoff](docs/student-api-handoff.md) for setup, JavaScript/Python
clients, response rules, dataset preparation, and Docker deployment.
Remote access still requires a reachable hosted or shared-network server.

**Nearest recorded wildfire:** choose **Wildfire / Nearby fire frequency** in the
hazard dropdown, then select a point to see the closest reported fire
across all available USFS FPA FOD and NIFC WFIGS history, with no date filter.
The red marker and dashed line show the reported fire location and its distance
from the selection. Results include discovery date, reported size, source record
and last checked time. The browser checks for updates every five minutes while
visible; **Refresh fire history** retries a lookup. Historical archive coverage
currently starts in 1992, with WFIGS supplying ongoing incident updates. See
[sources, coverage and search method](docs/fire-history.md).

The same wildfire view now includes **Annual nearby-fire frequency**: the share
of published archive years with at least one reported fire within **5, 10 or
20 km**. The current baseline is **1992–2024 (33 calendar years)**, with each year
counted once per radius. Select a radius to draw it on the map, inspect all years,
or export the annual calculations to CSV. These are historical reporting
frequencies with an explicit coverage assumption, not calibrated future burn
probabilities. Ongoing WFIGS reports are excluded from this annual baseline.

**Since last week:** the [weekly research and development update](docs/weekly-update.md)
summarizes the latest implementation, research findings, verification and open
questions. A [Word report](<docs/Natural Disaster Risk Analysis Weekly Update.docx>) is also available.
The [presentation](<presentations/Natural Disaster Risk Analysis Since Last Week.pptx>)
covers the same changes with editable charts, speaker notes and source links.

**Satellite fire observations:** with **Wildfire / Nearby fire frequency**
selected, the map also shows NASA FIRMS VIIRS hot spots near the point for the
last 24 hours, 48 hours or 7 days, within 10–100 km. Detections are thermal
anomalies, not confirmed wildfires. See [NASA data](docs/nasa-data.md).

**Historical Heat hazard:** choose **Heat (historical annual exceedance)** and
select a **Point**. NOAA GHCN-Daily maximum temperatures supply a historical
annual exceedance curve for 1991–2020 at a qualifying nearby US station. The
view includes trigger comparisons, a historical-frequency contribution
illustration, station distance, complete/excluded years and a CSV export.
At least 20 fully observed years are required; missing coverage stays missing.
This first version supports points, with no area heat curve or automatic
heat ranking. A collapsed **Latest heat conditions** section retains the optional
current METAR Heat Index context. Outdoor construction assessment needs on-site
WBGT and work-specific inputs; annual air-temperature maxima do not define
multi-day heat waves or shutdown criteria.
See [heat and wind rationale, sources and limitations](docs/heat-and-wind.md).

The fireline-intensity, **potential intensity (kW/m)** and **flame length (FSim)**
wildfire options have been removed from the map (the last two on November 10,
2026), and "Choose most relevant" no longer selects flame length. Since API 1.13.0
the public API no longer offers them either: its `wildfire` hazard uses the nearby
reported-fire method (acres within a chosen distance up to 20 km, share of years). Their research
notes remain ([TxWRAP record](docs/fireline-intensity.md),
[potential intensity method](docs/potential-fire-intensity.md)).

The app includes a **versioned research API** for hazard lookup, point trigger
comparisons, area analysis and nearest monitoring stations. After starting
`server.py`, open the [interactive API explorer](http://127.0.0.1:8000/api/docs)
or the [OpenAPI specification](http://127.0.0.1:8000/api/v1/openapi.json).
See the [API guide](docs/api.md) and run `python examples/api_client.py` for a
live comparison with optional JSON/CSV exports. This first version runs locally
and uses the same datasets and providers as the map.

Point API requests accept **a full US street address or a latitude/longitude
pair**. For example, run `python examples/api_client.py --address "100 Dolorosa St, San Antonio, TX" --peril flood --payout 50000`.
Extra whitespace is normalized. Responses display the Census matched address
and approximate coordinates; spelling corrections are not guaranteed. Unmatched
or ambiguous addresses stop the calculation with an explicit message. See the
[address-input guide](docs/api.md#street-addresses-spaces-and-typos).

Choose **Flood, Earthquake, Fire / Wildfire, Wind (tropical cyclones), Rainfall, or Heat** in
the dropdown, then click the map or search a US street address. Changing the
disaster reloads the curve and model details at the selected coordinate. Every
graph puts **frequency on X** (increasing left to right) and **severity on Y**.
Wind covers tropical-cyclone sustained wind only; Rainfall uses 24-hour accumulation depth.

Enter an address and choose **Stations (n)** (1–50, default 5) to find the nearest
**NOAA Aviation Weather Center METAR stations within 200 km**. Orange circles and
a distance-sorted list show station names, network, report variables, distances,
data links, and latest available observations with units and UTC times. Missing
reports and reports older than two hours are labeled. The worldwide METAR catalog
is independent of the selected hazard; Census address search covers US street
addresses. Rectangular selections use the **area center**. **Show selection and
stations** fits every result and the selection. Fewer eligible stations are
reported explicitly. See [monitoring-station details and limits](docs/monitoring-stations.md).

The map uses `/api/v1/monitoring-stations?network=awc_metar&lat=29.4241&lon=-98.4936&n=5`.
The legacy independent `/api/monitoring-station?peril=flood&lat=29.4241&lon=-98.4936`
endpoint searches within **200 km**: flood uses USGS active stream gauges with
gauge-height records (00065), wind/cyclone uses NOAA Aviation Weather Center
METAR stations, and rainfall uses USGS active atmospheric/stream gauges with
precipitation records (00045). Earthquake uses the [EarthScope FDSN station
service](https://service.earthscope.org/fdsnws/station/1/) with seismic/accelerometer
channels and operating epochs covering the current time. Wildfire uses the
[NIFC public interagency RAWS catalog](https://www.arcgis.com/home/item.html?id=29185087b4594a35abe059cbdbf97ee4),
restricted to stations listed active. Nearest means nearest in that network and radius,
not every station operated by every agency. Metadata is cached for up to ten
minutes and reused for nearby clicks. The search starts within 20 km, then expands
to 75 km and 200 km only if needed. Distances always use the exact selection.
If the station service stalls, the UI stops waiting after 15 seconds and offers
**Retry station search** without reloading the hazard curve.
Current readings and sensor availability are not checked. Flood gauges
may be in another watershed and do not measure inundation depth at the selected
property. These stations provide context and do not supply or change the hazard
curve, trigger probability or payout calculation. No results and service failures
are displayed explicitly. Seismic stations monitor ground motion and do not predict
earthquakes. RAWS monitor fire-weather conditions, not fire occurrence or flame
length at the selected location. Catalog status and seismic operating epochs do
not guarantee live telemetry. Wildfire cameras and satellite detections are not
part of this station lookup. Station details link to the seismic metadata or to a
NOAA station page, with a NIFC catalog-record link when no NOAA link is supplied.

Use **Select a → Rectangular area** above the map to select an area by clicking
two opposite corners. The outline previews as you move the pointer. **Draw new
area** replaces it; **Cancel drawing** or Escape keeps the previous selection.
Switch to **Point** for individual locations. Address searches select a point.
Area mode calculates a hazard curve at every location on a **5×5 grid** inside
and on the boundary, including the center. For each **severity**, it evaluates
the exceedance probability at every sample, then displays the **arithmetic mean
of the available probabilities**: sum of probabilities ÷ valid sample count.
All five hazards use the same averaging method. The app retains the previous
trigger when available; otherwise it selects the level with the highest average.
The selected rectangle's centroid is reported separately. Blue map dots have a
value at the selected trigger; gray dots are unavailable. Expand **Point-by-point
data for the selected trigger** to inspect all 25 locations and the mean calculation.

Exact source levels are used directly; other locations use log-probability
interpolation in severity within complete, monotone source brackets. Missing
levels, unsupported tails, and zero-frequency interpolation brackets are omitted.
Each trigger reports its evaluated, missing, and interpolated sample counts.
Missing samples are excluded from the sum and denominator; valid zero probabilities
are included. No valid values means no average. Results are an equally weighted
grid-point average, not an exhaustive raster-cell average, an area-weighted mean,
or the probability of an event anywhere in the area. Coverage can differ by
severity, so each averaged curve point shows its contributing sample count.
The API retains each location's raw source points and the values used at every
threshold. **Export area averages and samples CSV** includes an average row and
all 25 sample rows for every plotted severity, with missing values left blank,
coordinates, source/interpolation method, sum, counts, and bounds. The exploratory flood-depth,
damage, FEMA context, and **area center CSV** remain specific to the area centroid.
Automatic hazard selection uses county context at that centroid, then holds the
chosen hazard fixed across the grid.

The **Set your trigger and payout** panel lets you select a hazard, choose a location
on the map or by address, then select a supported trigger level. Its result card
shows the probability of meeting or exceeding that level in **one year**, with
the location, units, source, and method. Wind results are explicitly labeled
historical annual frequencies, and so are wildfire triggers (share of years with a reported fire of at least the chosen size nearby). Trigger
levels in point mode come directly from the loaded government points, without interpolation
or extrapolation. Missing levels are omitted, and missing coverage never becomes
0%. Changing the hazard, location, or earthquake site class clears the result.
The flood table's manual edits and illustrative examples do not alter the
government trigger results.

Select or enter a **payout if the trigger occurs (USD)** to calculate the
**expected annual pool contribution = (annual exceedance probability / 100) × payout**.
The exceedance amount is the fixed payout when the selected trigger is met or
exceeded. For example, a 1% probability and $50,000 payout give $500 per year.
The calculation assumes one fixed payout during a year and excludes fees and
other pricing adjustments. Probability ranges produce contribution ranges;
historical cyclone frequencies retain their observed-frequency label. Blank or
invalid payouts and unavailable triggers produce no contribution estimate.
Payouts remain selected when you change the hazard or location, while the
contribution clears until a new supported trigger is selected.

**Compare trigger levels** shows all supported thresholds for the same location
or area side by side, with their annual exceedance percentages, the shared fixed
payout, and expected annual contributions. Entering or changing the payout updates
every row; blank or invalid payouts leave contributions blank. **Use trigger**
selects that row in the existing trigger panel, and either control highlights the
active row. Rows are alternative contract choices, not contributions to add together.
The comparison preserves source probability ranges, wildfire estimate labels,
and cyclone historical-frequency labels. Area rows show the contributing,
unavailable, and interpolated sample counts because coverage can differ by
threshold; these averages are not probabilities of an event anywhere in the area.
Changing the location, hazard, or earthquake site class clears stale rows while
new data loads. Missing coverage never becomes a zero probability.

**Choose most relevant** keeps automatic selection active as you move between
locations. It ranks supported disasters using FEMA National Risk Index county
expected annual loss, then checks for usable local curves. If inland flooding
leads but the local flood-depth curve is unavailable, it displays rainfall as
related context. The selection explanation identifies the county, source and
any substitution. Missing coverage is not interpreted as low hazard. If the
county ranking is unavailable, an available-data fallback is labeled explicitly.

Run the app from this folder (the existing virtual environment and CONUS fire
rasters are already installed in this workspace):

```powershell
.\.venv\Scripts\python.exe server.py
```

Open [NDApp locally](http://127.0.0.1:8000). After Python backend changes, stop an
older server with Ctrl+C and restart it; refresh the browser to load UI changes.

| Selection | Severity (Y) | Government source and frequency (X) |
| --- | --- | --- |
| Earthquake | PGA (%g) | USGS NSHM static curves; AEP = 1 − exp(−annual exceedance rate), with explicit site class |
| Flood | Inundation depth (m) | Installed TWDB Texas 2025 depth rasters at 20%, 10%, 1%, 0.2% AEP; FEMA zone context |
| Fire / Wildfire | Flame length (m) | Original USFS FSim LF2020 BP and FLP5/FLP6 rasters; approximate BP × conditional exceedance at 8 ft and 12 ft; WHP class context |
| Wind (tropical cyclones) | Sustained wind (kn) | NOAA NHC HURDAT2 quadrant footprints at 34, 50, 64 kn; fraction of complete years since 2004 with a sampled footprint |
| Rainfall | Maximum 24-hour depth (mm) | NOAA Atlas 14 annual-maximum-series point estimates; AEP = 1 / return period |
| Heat | Annual maximum daily air temperature (°C) | NOAA GHCN-Daily TMAX at a qualifying nearby US station; observed share of complete 1991–2020 years meeting/exceeding each supplied threshold; point mode only |

The viewer route is `/api/disaster?peril=earthquake&lat=29.4241&lon=-98.4936&site_class=BC`.
It returns points, metric, units, frequency definition, source, model details,
and an explicit unavailable state when coverage is missing. The selectors have
different coverage; no example curves replace missing government data.
Wind frequencies are historical tropical-cyclone footprint samples, not calibrated future
AEP or peak gusts. Fire probabilities are research approximations, not daily
fire-weather forecasts. See [viewer methods](docs/disaster-viewer.md).

For a fresh installation, install `requirements.txt`. Install fire rasters using
`python prepare_fire_data.py --region CONUS` (about 325 MB compressed). `HI` and
`AK` can be installed separately. Download size/CRC and SHA256 provenance are
recorded in `data/usfs/*.json`.

Run Python verification with `python -m unittest discover -s tests -v` after
installing `requirements-dev.txt`, and chart verification with
`node tests/test_hazard_chart.cjs`.
Run trigger verification with `node tests/test_hazard_trigger.cjs`.

NDApp now also includes a [four-peril parametric hazard design](docs/parametric-hazard-design.md),
an executable Python pricing/trigger engine, a [location JSON Schema](schemas/hazard-location.schema.json),
and a [PostgreSQL/PostGIS database blueprint](database/hazard_schema.sql). The separate
`/api/hazards` research endpoint supports USGS PGA curves and installed TWDB flood
depths; calibrated rain/wind and wildfire datasets are still required. Run
`python examples/hazard_demo.py` for an illustrative pricing and trigger example,
or install `requirements-dev.txt` and run `python -m unittest discover -s tests -v`
for the verification suite. `python verify_hazard_api.py --live-usgs` checks the
HTTP/data contract and saves a real San Antonio research response.

A small Leaflet proof of concept. Search a US street address or click the map to query FEMA's National Flood Hazard Layer (NFHL) polygon attributes and, when local TWDB raster tiles are installed, automatically fill four flood-depth scenarios for the same point. A selected single-family home profile can turn those depths into exploratory structure-damage percentages. The insurance panel uses your selected fixed payout to calculate an expected annual pool contribution.

## First-time setup (San Antonio)

Use Python 3.10+ in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe prepare_data.py
.\.venv\Scripts\python.exe server.py
```

Open <http://127.0.0.1:8000>. Keep the server terminal open. The `prepare_data.py` step downloads the **n29w99** tile containing central San Antonio for each of four events. TWDB archives are large (hundreds of MB apiece), so allow time and several GB of free disk space. Downloaded GeoTIFFs live in `data/twdb/`, which Git ignores. The script can be rerun after an interruption and skips completed rasters. It removes each archive after extraction to reduce disk use.

For a **Texas** point in another 1-degree tile, run `python prepare_data.py --tile nXXwYY` with the tile corresponding to the point's southwest integer-degree corner. For example, latitude 29.4, longitude -98.5 is `n29w99`. TWDB does not cover other states. The map and US Census address search accept US locations; the FEMA service returns flood-zone polygons where digital NFHL coverage exists. The current depth and damage curves are Texas-only.

## How the automatic lookup works

### Address search

Enter a **full US street address with city and state** and press **Find address**. The local server sends that query to the [US Census Geocoding Services single-record address endpoint](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html) using the `Public_AR_Current` benchmark. Its returned coordinates are interpolated from address ranges, so the marker is approximate; confirm the point on the map before interpreting flood results. The search runs only when you submit the form, and it shows a clear message for no match or service failure. City names alone may not match. Search terms are sent to the Census Bureau; use the map click if you prefer not to submit an address.

The search selects the point and triggers the **same** FEMA and TWDB lookup as a map click. FEMA's national service has gaps in digital map coverage; the TWDB depth rasters are Texas data and each area requires its local tile to be installed.

The browser requests `/api/depths?lat=...&lon=...` after each map click. `server.py` determines the tile, then Rasterio samples the click coordinate from each installed TWDB GeoTIFF. TWDB's [2025 Cursory Floodplain Dataset](https://twdb-flood-planning-resources-twdb.hub.arcgis.com/pages/cursory-floodplain-data-2025) identifies existing conditions as **scenario 5**. This app uses its **combined-peril** 3-meter depth rasters for the 20%, 10%, 1%, and 0.2% annual-chance events. The [TWDB data structure guide](https://s3.amazonaws.com/twdb-gis-data/Staged/office-of-planning/flood-planning/flood-data/flood-risk/cursory-floodplain-data-2025/Cursory_Floodplain_Data_Structure_Phase_2.pdf) says the raster CRS is WGS84/EPSG:4326, depths are in feet, and zero can mean either NoData or no associated modeled flood risk. Consequently, the app leaves zero and missing cells **blank** and labels them “No mapped depth / NoData”; it never treats them as evidence of zero flood risk. TWDB applies a 0.5-foot threshold.

The archives come from [TWDB's download catalog](https://s3.amazonaws.com/twdb-gis-data/index.html#office-of-planning/flood-planning/flood-data/flood-risk/cursory-floodplain-data-2025/Scenario_5_Existing_Conditions/Fathom_3m_Combined_Peril_Scenario_5_Depth_Raster_Tiles/). `prepare_data.py` retrieves the matching `n29w99_2020_0p50_combined_1in5.zip` and equivalent `1in10`, `1in100`, and `1in500` archives. The `2020_0p50` substring is part of TWDB's published filename; the containing folder identifies **scenario 5 existing conditions**. The app does not interpolate between event frequencies.

## FEMA query

The separate FEMA lookup uses the public [NFHL ArcGIS REST MapServer](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer), layer **28, Flood Hazard Zones (S_Fld_Haz_Ar)**. It sends a point query in EPSG:4326, ordered longitude,latitude, requesting `FLD_ZONE`, `ZONE_SUBTY`, `SFHA_TF`, `STATIC_BFE`, and `DEPTH`. Empty `features` produces the no-polygon message. FEMA's zone attributes do not supply the four-event depth curve.

The basemap uses [OpenFreeMap's OpenStreetMap-derived vector tiles](https://openfreemap.org/quick_start/) through a Leaflet adapter to avoid the 403 block seen with the volunteer-run OSM raster tile server. An internet connection is needed for the basemap and FEMA. Once downloaded, TWDB depths are sampled locally.

## Nationwide data plan

The existing NFHL point query is a useful US-wide regulatory-map lookup, subject to [digital coverage gaps](https://www.fema.gov/flood-maps/national-flood-hazard-layer). It does **not** provide a consistent nationwide four-event depth curve. [FEMA flood-depth grids](https://www.fema.gov/sites/default/files/documents/fema_flood-depth-grids.pdf) are made for individual studies, and available event frequencies vary by place. Do not substitute flood-zone codes or `-9999` attributes for measured depths.

For comparable point-level frequency–depth curves across the US, use a licensed national model behind a server-side data-provider interface. [Fathom's US Flood Map API](https://www.fathom.global/product/global-flood-map/us-flood-map/) offers return-period depths; its [trial access](https://api-docs.fathom.global/onboarding.html) includes full data in Houston, New Orleans, and Tampa tiles, but national access requires a license. [First Street's API](https://docs.firststreet.org/api/climate-risk-api/retrieving-data/flood-probability-and-damages) provides return-period depths by coordinate or property, also under licensed access. Coverage details differ by provider and should be checked for Alaska, Hawaii, and territories. Keep provider name, model version, event definition, units, and no-data status with every result. Until a provider is selected, the app leaves depths and modeled damage blank outside installed TWDB Texas tiles.

## Structure-damage estimate and CSV

Choose one of four **RES1 single-family residential** profiles (one or two stories, with or without basement), enter the first-floor height above ground in feet, and check the Hazus estimate box. For each positive sampled depth, the app subtracts that floor height and linearly interpolates the corresponding **FEMA Hazus 4.0 riverine building** damage curve. The percentages represent estimated **structure damage as a share of building replacement value**, not contents damage, dollar loss, or an insurance payout. Blank TWDB depths remain blank damage estimates. The app does not extrapolate beyond the curve's -4 to 24-foot range. With the checkbox off, damage can still be entered manually.

The four function IDs are **129** (one story, no basement), **704** (one story, basement), **107** (two stories, no basement), and **108** (two stories, basement). Coefficients are from FEMA's archived [FAST Hazus 4.0 riverine building lookup table](https://github.com/nhrap-hazus/FAST/blob/main/Lookuptables/Building_DDF_Riverine_LUT_Hazus4p0.csv). [FEMA's Hazus 7 Flood Model Technical Manual](https://www.fema.gov/sites/default/files/documents/fema_rsl_hazus-7-fltm_06272025_0.pdf) still lists these function IDs; the app uses the older **4.0 coefficients** and does not claim they are Hazus 7 coefficients. Applying riverine functions to TWDB's combined-peril modeled depths is an exploratory research assumption. Actual damage depends on additional building characteristics, flood duration, velocity, and local conditions.

After selecting a map point, **Export selected point CSV** downloads its four scenario rows with coordinates, depth and status, damage and method, building inputs, FEMA attributes, and notes. It works locally in the browser. The **Load illustrative example** button uses invented values only to demonstrate the charts; its values should not be interpreted as location data.

## Ten-point San Antonio cross-check

Run `python validate_points.py` with the virtual environment to reproduce the [ten-point CSV](validation/san_antonio_points.csv). The script selects five positive and five blank TWDB 1% cells in a central San Antonio box with a fixed seed, samples all four installed rasters, and queries FEMA NFHL layer 28 at each point. See [validation notes](validation/README.md) for the results and limits. This is a data-flow and map-comparison check, not validation against observed flood events or damage.
