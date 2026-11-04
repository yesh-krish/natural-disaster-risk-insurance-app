# NASA fire observations, fire tracking, and surface heat: NDApp evaluation

Evaluated **November 3, 2026 (America/Chicago)**. Recommendation: first add a
**NOAA-20 VIIRS Collection 2 active-fire observation overlay**, explicitly separate
from hazard curves and payouts. Follow with FEDS event histories if needed. Use
land-surface temperature (LST) in a separate surface-heat research view.

This evaluation changes documentation and saves research samples only. No NASA
provider, UI, credentials, production data, calculation, or dependency was added.
The sample reproduction script does not import the app.

## 1. App and research context read before product selection

Read `README.md`, `docs/heat-and-wind.md`, `docs/fireline-intensity.md`,
`docs/hazus-fire-research.md`, `docs/parametric-hazard-design.md`, and relevant
architecture/API notes. Inspected the relevant implementations below. No applicable
`AGENTS.md` was found in the workspace/docs or ancestor directories.

| Existing code | Actual quantity and implication for NASA integration |
| --- | --- |
| [`disaster_data.py`, `wildfire()`](../disaster_data.py) | USFS FSim LF2020, native 270 m BP/FLP5/FLP6. Estimates `BP × (FLP5+FLP6)` at 8 ft and `BP × FLP6` at 12 ft, then converts to percent and meters. These research annual flame-length exceedance estimates are not observed fire activity. |
| [`fireline_intensity.py`](../fireline_intensity.py) | TxWRAP native weather-weighted mean heading fireline intensity, kW/m, with sample/metadata hashes. Correctly supplies no annual probability. NASA FRP cannot replace this metric. |
| [`heat_context.py`](../heat_context.py), [`monitoring_stations.py`](../monitoring_stations.py), [`script.js`](../script.js) | Nearby METAR air temperature/dew point, derived relative humidity and shaded NWS Heat Index. Report timestamps and the two-hour screening freshness rule matter. Satellite LST must not enter this calculation as air temperature. |
| [`area_hazard.py`](../area_hazard.py), [`hazard_trigger.js`](../hazard_trigger.js), [`api_v1.py`](../api_v1.py) | Existing 5×5 sampling averages compatible probabilities, or native intensity in its separate case. Contributions use annual probability/frequency × fixed payout. Hotspot counts, FRP, and LST are not inputs to that arithmetic. |
| [`hazard_engine.py`](../hazard_engine.py), [`hazard_providers.py`](../hazard_providers.py) | Separate research engine pins metric definitions, source, event, quality, revisions, and time windows. Its wildfire provider remains unavailable. A NASA observation does not supply the missing compatible annual intensity model. |

The existing notes already distinguish fireline intensity, flame length, burn
severity, and FRP. They also identify missing multi-year heat-wave data and
worksite WBGT inputs. The following assessment preserves those boundaries.

## 2. Definitions that control what the products can support

**A satellite active-fire detection is a thermal-anomaly observation at an
overpass.** Its coordinate represents a detected pixel, not an ignition point,
incident identity, exact flame location, or verified burned footprint. Fire can
occupy only part of a pixel. Prescribed/agricultural fires, industrial heat,
gas flaring, and volcanoes can appear alongside wildfires. Small, cool,
short-lived, obscured, or between-overpass fires can be missed. NASA's training
explicitly warns that the thermal signal alone does not identify its cause.
[NASA FIRMS training Q&A][F7], [Schroeder et al. (2014)][P1]

**FRP is pixel-integrated fire radiative power, in MW.** It measures the radiative
component of energy release per unit time at observation, not all combustion
heat. Byram fireline intensity is heat release rate per unit fire-front length,
`I = H × w × R`, in **kW/m**. Reaction intensity and received heat flux use
**kW/m²**, with different meanings; flame length uses meters; burn severity
describes effects. None is obtained by merely renaming FRP or converting MW to
kW. Converting FRP to fireline intensity would require an independently justified
radiant fraction, contributing front length, geometry, and matched timing.
Temporal integration of FRP yields fire radiative energy (FRE, e.g. MJ), but sparse
overpasses alone do not establish a complete energy or fuel-consumption history.
[Wooster et al. (2005)][P4], [existing intensity definitions](fireline-intensity.md)

VIIRS FRP uses a hybrid of 375 m detection and 750 m radiometry; neighboring
375 m detections can share a partitioned 750 m FRP estimate. They are not
independent fine-scale intensity measurements. Saturation or insufficient
background can produce zero/null FRP despite a detection; preserve that caveat.
[VIIRS active-fire guide, §2.2][F4]

**LST is radiometric surface skin temperature**, representing the roof, soil,
vegetation canopy, or other surface seen by the instrument. Near-surface air
temperature is a different measurement, usually taken above ground in a
shielded environment. LST is useful for surface heat patterns, energy balance,
and surface urban heat islands; it is not directly air temperature, Heat Index,
WBGT, physiological exposure, or a heat-wave forecast. A valid surface-to-air
model would require additional inputs and local validation.
[NASA surface-temperature explanation][L1], [Hulley et al. (2018)][P5]

## 3. Track A: active-fire observations and fire-event tracking

### Detection products

Resolution is nominal sampling support, not property-level accuracy. An overpass
cadence is distinct from delivery latency, and neither guarantees cloud-free
observations.

| Product | Coverage and spatial/temporal support | History and latency | Can support / cannot support |
| --- | --- | --- | --- |
| **FIRMS MODIS C6.1**, Terra/Aqua; `MODIS_NRT` and `MODIS_SP`; MOD14/MYD14 source family | Global; nominal **1 km at nadir**, larger toward scan edge. Discrete day/night overpasses; combining platforms improves sampling. | FIRMS record starts **2000-11-01**; Aqua contributes from 2002. NRT global delivery is best effort, generally within **3 hours after observation**. SP is the retrospective science stream; inspect actual availability. | Long fire-observation context and FRP comparisons within a consistent sensor/collection. Cannot resolve exact burned area from pixel count, identify each incident, or supply annual fireline-intensity probabilities. [F2], [F7], [P2] |
| **FIRMS VIIRS C2 375 m**, S-NPP/NOAA-20/NOAA-21; `VIIRS_SNPP_*`, `VIIRS_NOAA20_*`, `VIIRS_NOAA21_*` (`NRT`/`SP`) | Global; nominal **375 m at nadir**, actual `scan`/`track` vary. Typically day/night observations per platform, roughly half-daily; overlapping platforms are additional samples, not continuous monitoring. | NASA training gives S-NPP **2012-01-20**, NOAA-20 **2018-04-01**, NOAA-21 **2024-01-17**. Particular services/processing streams may start later; query availability. NRT target as above; regional RT/URT can be faster. | Preferred first observation overlay: improved response to smaller fires and spatial detail over MODIS. Cannot certify a property burned, supply a precise perimeter, or distinguish wildfire from another heat source unaided. [F3], [F7], [P1] |

NASA's current [Area API][F1] defines RT as within 60 minutes and advertises URT
under 60 seconds for much of the US/Canada. These are regional capabilities,
not a global service guarantee. Older NASA support guidance gives different
RT/URT timings; actual source status and acquisition-to-ingestion age should
govern the app. Current API documentation also says RT/URT records are removed
when matching NRT arrives or after six hours. A production cache must reconcile
revisions rather than append every processing stream as another fire. [F1], [F2]

The nominal original local overpass schedules are not timestamps to manufacture
for a record. Use the actual acquisition date/time; mission orbit changes,
missing granules, and processing outages affect coverage. NRT can be revised
and has different ancillary/calibration inputs from standard science processing.
Use a pinned SP collection for retrospective research, preserve source changes,
and do not concatenate a changing constellation into a homogeneous fire-count
trend. [LANCE guidance][A4], [MODIS algorithm/validation study][P2]

### Required observation fields and quality handling

| Field | Meaning and proposed handling |
| --- | --- |
| `latitude`, `longitude`, `scan`, `track` | Pixel center in geographic WGS84; footprint dimensions in km. Retain footprint metadata and location uncertainty. A point-in-box query is not proof of footprint intersection. |
| `acq_date`, `acq_time` | Acquisition date and UTC HHMM. Read time as text, left-pad to four digits, validate, and combine as UTC. `0745` is 07:45 UTC, not a decimal hour or publication time. |
| `confidence` | MODIS uses a 0–100 algorithm score: low <30, nominal 30–<80, high ≥80. VIIRS uses low/nominal/high, commonly `l/n/h` in API output and full words in downloadable CSVs. Do not apply numeric MODIS thresholds to VIIRS. |
| `frp` | MW; retain raw value and source-specific missing/saturation semantics. No universal “severe fire” threshold is proposed. |
| `brightness`, `bright_t31` or `bright_ti4`, `bright_ti5` | Channel brightness temperatures in K; neither flame temperature nor ambient air temperature. |
| `satellite`, optional `instrument`, `version`, `daynight` | Preserve platform, collection/processing suffix, D/N. The sampled download has no `instrument` column; derive it explicitly from the selected product, not a guessed column. |

Field definitions: [NOAA-20 NASA product specification][F3], [MODIS C6/C6.1
guide][F5], and [NASA ingestion examples][F6]. Confidence indicates algorithm
quality; it is not the annual probability of fire or a calibrated probability
that a particular property is burning. VIIRS high confidence is associated with
saturation, so selecting only high confidence can remove useful nominal
detections. Proposed default: show nominal/high, retain low records for an
optional diagnostic layer, disclose excluded counts, and validate the filter for
the intended region/use. [F3], [F4]

Clouds, thick smoke, canopy obscuration, scan geometry, mixed pixels, sun glint,
and detector saturation create omissions or artifacts. Fire-only CSVs omit the
full cloud/no-observation/nonfire mask. Therefore **zero returned detections is
“no detections in this source/query,” not “no fire.”** For coverage-aware research,
obtain the underlying swath fire mask/QA and observation coverage. Repeated
detections are repeated samples of activity, not separate incidents. [P1], [P2]

### Event and burned-area products

| Product | Geography, resolution, time and history | Appropriate use and limits | Access / quality |
| --- | --- | --- | --- |
| **FEDS / VIIRS Modeled Fire Perimeters** | Evolving vector objects derived from VIIRS 375 m detections; **12-hour** modeled steps. The original validated release covers California **2012–2020**. Current team documentation lists Western US **2018–2021** and North America from **2025**, with FIRMS displaying the current calendar year. | Best fit for later incident-like histories: evolving modeled extent, active segments, area and associated detections. Alpha-shape clustering can merge or split fires differently from agency incidents. These are modeled boundaries, not surveyed/operational perimeters or precise emergency-response boundaries. [P3], [T1] | Public VEDA OGC Features API and NASA map layers. Store regional/year identifiers, processing version and revisions; inspect `low_confidence_grouping`. Cloud gaps and clustering choices propagate into extent, timing and growth. [T2], [T3] |
| **Global Fire Atlas, archived ORNL release** | Global, **500 m**, daily progression derived from MODIS burned-area dates; published archive **2003–2016**, DOI 10.3334/ORNLDAAC/1642. | Useful for retrospective event size/duration/progression research. Event segmentation and daily burn dates are inferred; it is not a live incident feed or FRP product. Larger fires are better represented than small/fast fires. Later research extensions exist; pin the selected release rather than assume the ORNL archive extends to today. [T4], [P6] | Archived GIS/raster/tabular downloads; protected ORNL downloads require Earthdata Login. Inherits burned-area omissions/date uncertainty, including missed small burns. Historical validation, not an operational SLA. |
| **MCD64A1 C6.1 burned area** | Global, **500 m**, monthly files with estimated burn day-of-year; record from **November 2000**. | Retrospective mapped burning and possible event-clustering input. Not itself a canonical incident database, live perimeter, burn-severity map, or instantaneous intensity. | Earthdata/LP DAAC; retain BurnDate uncertainty, QA, FirstDay/LastDay and valid-data/shortened-period flags. Monthly processing and revisions make it unsuitable for immediate detection alerts. [T5], [P7] |

**FEDS availability caveat.** Current team documentation contains older
CONUS/Canada wording alongside newer North America descriptions (CONUS, Alaska,
Canada and northern Mexico); an older NASA ArcGIS description lists geographic
exclusions and ~9–12-hour latency. The current team FAQ instead describes output
within about **4 hours after FIRMS input becomes available**. These refer to
different publication stages/versions, so no single verified end-to-end SLA is
claimed. Confirm collection/region/year before promising coverage, especially
Alaska/Hawaii/Mexico. A generic worldwide bbox in API metadata does not establish
worldwide FEDS production. [T2], [T3], [T6]

For legal/contractual or operational burned-footprint questions, separately
approved agency perimeters and revision/finality rules would be necessary.
Even a precise perimeter would not establish flame length or kW/m at a property.

## 4. Track B: land-surface temperature for heat patterns and anomalies

These are thermal-infrared retrievals with clear-sky and emissivity/atmospheric
limitations. “Daily” means a product interval, not continuous observation, daily
maximum air temperature, or complete cloud-free coverage.

| Product / version | Coverage, resolution and time | History / delivery latency | Benefit and limitations |
| --- | --- | --- | --- |
| **MOD11A1 / MYD11A1 .061** (Terra/Aqua) | Global land; nominal **1 km** sinusoidal daily day/night L3. Above 30° latitude, qualifying multiple observations may be averaged. | NASA CMR starts: Terra **2000-02-24**, Aqua **2002-07-04**. Standard archive processing is distinct from LANCE NRT; no fixed per-granule delivery SLA verified here. | Longest candidate record for seasonally matched surface-heat climatology. Retrieve LST, QC_Day/Night, view time/angle and clear-sky coverage. Surface/emissivity biases, clouds and mixed land cover limit fine urban interpretation. [L2], [L3], [L4] |
| **MOD11A2 / MYD11A2 .061** | Global; nominal **1 km**, **8-day** averages of available daily LST, separate day/night. Grid spacing is approximately 926.6 m, not an independent 1 km accuracy claim. | Terra collection metadata begins **2000-02-18**; inspect actual granules. Composite cannot be complete until its interval ends, plus processing/subsetting delay. | Convenient historical pilot through TESViS; smooths clouds/gaps but also short extremes. Cannot establish daily heat-wave duration, all-day mean temperature, or continuous exposure. [L3], [L4], [A1] |
| **VNP21A1D / VNP21A1N .002** (S-NPP VIIRS; NASA TES LST) | Global; native **750 m** observations resampled to **1 km** daily day/night products. Good cloud-free observations are coverage-weighted. | **2012-01-17 onward** in CMR. Science archive, with actual publication times to inspect; FIRMS fire latency does not apply. | Useful continuity/comparison candidate; dynamic temperature/emissivity separation. Preserve QC, view time/angle and emissivities. NASA warns about extreme high outliers in unfiltered VNP21/MxD21; do not select maxima before filtering. [L5], [L6], [P5] |
| **ECOSTRESS ECO_L2T_LSTE .003** (current tiled product; .002 is legacy) | ISS coverage described as approximately **52°S–52°N**, including Texas. **70 m** gridded COGs; instantaneous scenes at varying local times. Mission literature describes roughly **3–5-day** revisit, variable by location/acquisition; usable clear scenes can be much less frequent. | CMR starts **2018-07-09**; Version 3 is a current 2026 release. Variable acquisition/downlink/processing and gaps; no fixed end-to-end latency verified. | Best spatial detail here for neighborhood surface patterns, but not guaranteed daily mapping or a complete long-term baseline. LST algorithm runs even on cloudy pixels: apply cloud mask explicitly. Keep QC, emissivity/error information, water mask, viewing geometry and scene geolocation/obstruction metadata. [L7], [L8], [L9] |

The [live CMR collection queries][L4] were checked for exact short names and
versions, not just mission launch dates. An open-ended collection does not prove
every date/location has a granule. VNP21A1D and N concept IDs are
`C2545314555-LPCLOUD` and `C2545314559-LPCLOUD`; ECOSTRESS V003 is
`C3998139651-LPCLOUD`. Discover the catalog each time an integration is configured.

**Quality policy.** Decode the selected product's own bits and scale/offset/fill
metadata; do not copy MODIS QA rules to VIIRS or ECOSTRESS. For MOD11, QC bits
0–1 indicate retrieval quality, 2–3 data quality, 4–5 emissivity-error class and
6–7 LST-error class. The sample below demonstrates a proposed strict research
filter. Error classes are algorithm quality indicators, not a confidence interval
for air temperature or property exposure. [MOD11 guide, Tables 13/18][L3]

Thermal retrievals sample clear conditions preferentially; missing clouds cannot
be filled with zero or silently interpolated. View angle, terrain, shadows,
land-cover mixtures and time of day alter the signal. ECOSTRESS also has permanent
historical acquisition gaps and known obstruction/geolocation issues. Version 3
changes formats and processing; do not use a Version 2 parser/QA interpretation
without checking the Version 3 documentation. [L7], [L8], [P5]

**Proposed heat-map/anomaly method, not implemented:** start with daily MOD11A1
for regional day/night surface patterns; use ECOSTRESS later for selected urban
case studies. Define `anomaly = accepted LST − baseline median LST` for the same
pixel, sensor/collection, day/night class and seasonal window (for example
2003–2022, ±15 calendar days). Require a documented minimum number of valid
years/observations, report coverage and baseline uncertainty, and omit inadequate
cells. The exact baseline/window/filter require study design and validation.
Daily MODIS and irregular-time ECOSTRESS anomalies must not share a baseline
without time-of-day harmonization. No anomaly is calculated from the single
sample retrieved here.

For a selected rectangle, aggregate accepted native cells with area weights in
an appropriate CRS and show coverage. Reusing the app's 25-point probability
mean would not create a proper raster heat map. Label a density heat map of
FIRMS points as **detection density**, with kernel/window/sensor stated; it is
neither surface temperature nor burned area. Cross-sensor counts and FRP sums
need overlap and temporal-sampling controls.

## 5. Access, credentials, service limits and use

| Service | Reproducible access and credentials | Limits / usage considerations |
| --- | --- | --- |
| FIRMS Area API | `GET /api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{days}/{YYYY-MM-DD}`. Free MAP_KEY requested with email; distinct from Earthdata Login. Date means the **start** of the requested interval. | Current documentation: **1–5 days/request**, **5,000 transactions/10 minutes**; large queries can cost multiple transactions. Inspect `/api/data_availability/csv/{MAP_KEY}/{SOURCE}` first. Do not hardcode historical availability. [F1], [A2] |
| FIRMS downloads / map services | Public rolling CSV downloads worked without a key in this evaluation; WMS/API use MAP_KEY. Long historical download workflows may require Earthdata Login. | Archive raw snapshots because rolling URLs change. Open-data use is encouraged with citation; NRT limitations and corrections remain. Keep keys out of browser code, logs and shared URLs. [F2], [A3] |
| FEDS VEDA OGC API | Public `/api/features/collections`, collection `/queryables`, and `/items`; no credentials needed in tested metadata and one-feature access. | Follow pagination and advertised query/filter support. Retain algorithm/release provenance and the underlying VIIRS/FIRMS attribution. No numerical service quota or availability guarantee was verified. [T3] |
| NASA CMR + LP DAAC / LAADS | CMR metadata searches are public; bulk/science-file download commonly needs free Earthdata Login/token and application authorization. | Pin collection, native granule IDs and revision; metadata availability does not grant anonymous file access. Protected S3 credentials are a separate temporary access mechanism, not a FIRMS key. [L4], [A5] |
| AppEEARS | Earthdata Login generates a Bearer token, valid approximately 48 hours; authenticated point/area tasks return selected science and quality layers. | Discover supported product/version/layers before submitting; poll task completion and download bundle. Version 3 support must be checked, not inferred from V002 availability. [A6] |
| ORNL TESViS REST | Public product/band/date discovery and small `/{product}/subset` JSON/CSV requests worked without credentials. Used for the sample below. | Maximum **10 dates/request**; spatial half-width/height up to **100 km**. This service is not an operational heat-alert endpoint. Cite both the original dataset and TESViS service. [A1] |

NASA promotes free, open Earth-science data and FIRMS reuse with attribution.
No dataset purchase or commercial-use fee was identified for the evaluated NASA
products. Preserve the collection's citation, DOI and any provider-specific
license/disclaimer; “public API” is not a service warranty. The paper's copyright,
software license, and any third-party hosting terms are distinct from data access.
No account was created, no key requested, and no credentialed historical or
large-file download was attempted. [NASA open-data policy context][A7],
[NASA FIRMS reuse guidance][A3], [NASA dataset license metadata][L5]

## 6. Retrieved samples and exact reproduction

All requests returned HTTP 200. Raw bytes, URLs, retrieval times, HTTP dates,
available Last-Modified headers and SHA-256 hashes are preserved in
[`samples/nasa/capture/manifest.json`](samples/nasa/capture/manifest.json).
This is a small access/interpretation demonstration, not satellite calibration
or validation of a hazard model.

### A. San Antonio regional thermal detections

- **Location:** WGS84 bbox west −99°, south 29°, east −98°, north 30°; includes
  San Antonio and surrounding south-central Texas. This is a regional box, not
  a property footprint.
- **Requested analysis interval:** **2026-11-02 00:00:00Z ≤ acquisition <
  2026-11-03 00:00:00Z**. Filtered locally from a rolling regional NOAA-20 CSV.
- **Downloaded:** **2026-11-03 22:57:23.922578Z**; HTTP Last-Modified
  **2026-11-03 22:41:29Z**. Neither is an acquisition timestamp.
- **Result:** 5 detections, all `N20`, `nominal`, `2.0NRT`; acquisitions at
  **07:45Z** and **20:44Z**; FRP **0.17–3.70 MW**. No event identity/cause or
  perimeter was established. These are not five confirmed wildfires.

Saved [five-row CSV](samples/nasa/capture/firms-san-antonio-2026-11-02.csv) and
[complete source snapshot](samples/nasa/capture/firms-noaa20-24h.csv)
(149,327 bytes, 1,761 rows). The source contains dates November 2–3; its filename
is not used as evidence of exact temporal coverage. Source SHA-256:
`fa3268823db96b75e7739509a010dcc93936d163bdb9cdb92dd5bd6cdff1582e`.

For example, the first detection is at **29.67559, −98.18747**, acquisition
**2026-11-02T07:45:00Z**, FRP **0.17 MW**, scan/track **0.33/0.55 km**.
It is a pixel observation, not a temperature at the selected San Antonio address.

### B. Historical San Antonio surface-temperature cell

- **Location:** **29.4241°N, 98.4936°W** (signed longitude −98.4936°), one
  MOD11A2 cell, tile **h09v06**.
- **Interval:** composite **A2024185**, **July 3–10, 2024 inclusive**; eight-day
  LST average, not an observation at midnight on July 3.
- **Downloaded:** **2026-11-03 22:57:25.456317Z** from public TESViS;
  `kmAboveBelow=0&kmLeftRight=0` returned 1×1 cell, spacing **926.625433 m**
  in sinusoidal coordinates. Retain returned grid origin and CRS interpretation.
- **Version provenance:** TESViS response omits the collection number. A saved
  CMR record matches product, date, tile and processing ID exactly to
  **`MOD11A2.A2024185.h09v06.061.2024195060944`**,
  concept **G3157364044-LPCLOUD**, establishing the matching C6.1 archive
  granule. Its full HDF payload was not downloaded or byte-compared with TESViS.

| Layer | Raw → physical value | QC interpretation | Proposed strict display |
| --- | --- | --- | --- |
| Day LST | `15635 × 0.02 = 312.70 K = 39.55 °C` | QC **81** = mandatory 1, data quality 0, emissivity error 1 (≤0.02), LST error 1 (≤2 K). | Withheld: fails mandatory-good and ≤1 K criteria. |
| Night LST | `15061 × 0.02 = 301.22 K = 28.07 °C` | QC **17** = mandatory 1, data quality 0, emissivity error 1, LST error 0 (≤1 K). | Withheld: fails mandatory-good criterion. |

The deliberately strict pilot filter is `(QC & 3)==0`,
`((QC>>2)&3)==0`, `((QC>>4)&3)<=1`, `((QC>>6)&3)==0`, plus valid non-fill
LST. It is a proposed app policy, not a NASA instruction to discard every other
retrieval. Less strict use requires a stated uncertainty policy. Clear-sky
day/night bitmaps are **11/7**; they are bitmaps, not counts of 11/7 days.
Both have three set bits. Composite quality does not imply eight clear days.
[MOD11 C6.1 guide, §§4.2/4.5][L3]

Saved [raw LST JSON](samples/nasa/capture/mod11a2-san-antonio.json),
[band metadata](samples/nasa/capture/mod11a2-bands.json) and
[matching CMR granule](samples/nasa/capture/mod11a2-cmr-granule.json).
LST-response SHA-256:
`aa02cfcb3be5aa5de3855160e5b34bf452654717bed2093ec8969de023c8187c`.
The decoded numbers are research inspection values, not accepted heat screening,
an anomaly, or station air temperatures.

### Commands

Run from the repository root. Offline reproduction requires only Python's
standard library; it verifies all four raw response hashes, source-granule
matching, the five-row subset and recomputed summary against the saved manifest:

```powershell
.\.venv\Scripts\python.exe docs\samples\nasa\reproduce.py
```

To repeat public HTTP retrieval into a **new** directory:

```powershell
.\.venv\Scripts\python.exe docs\samples\nasa\reproduce.py --download docs\samples\nasa\repeat-2026-11-03
```

The script refuses an existing target directory. A later run retrieves a changed
rolling fire feed and may return zero November 2 rows; the saved snapshot is
the exact reproduction source. Historical reprocessing may also change LST/CMR
responses. Each new run records new hashes/times; it does not overwrite evidence.

The [script](samples/nasa/reproduce.py) contains exact URLs and filters. For a
date-pinned FIRMS API repeat, obtain a personal MAP_KEY and use:

```text
https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{MAP_KEY}/VIIRS_NOAA20_NRT
https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/-99,29,-98,30/1/2026-11-02
```

For later historical research, select `VIIRS_NOAA20_SP` if that date is available,
and expect differences from the saved NRT snapshot. These keyed requests were
documented, **not executed**. No API quota or historical-key access is claimed.

FEDS access was separately checked anonymously: the [live collections][T3] and
[perimeter queryables][T7] were readable, and an unfiltered `items?limit=1` request
returned one feature. This verifies access/schema only, not coverage or completeness
at San Antonio. The observed schema includes `region`, `fireid`, `t`,
`low_confidence_grouping`, `farea`, and `meanfrp`; use advertised queryables and
documentation rather than assume the generic OGC `datetime` parameter is supported.

## 7. First integration recommendation and data contract

**First: a separately labeled “Satellite active-fire observations — NOAA-20 VIIRS”
layer/card**, backed by the FIRMS Area API. Engineering effort is modest relative
to processing thermal rasters or reconciling event polygons: the app already has
point/rectangle selection, asynchronous map overlays and provenance-aware cards.
Start with one platform and the NRT stream to make sampling and reconciliation
auditable; expand to NOAA-21/S-NPP after overlap/revision handling is verified.

Proposed behavior, **not implemented in this evaluation**:

1. A backend observation adapter accepts the selected rectangle or an explicitly
   labeled buffer around the selected point (proposed 25 km for a pilot). Query
   its enclosing bbox, then apply the exact buffer test if requested. Record
   whether matching uses pixel centers or footprint intersection.
2. Fetch a bounded acquisition window (proposed trailing 24 hours), cache per
   source/region/window, refresh on a measured interval (proposed 10 minutes),
   and keep MAP_KEY server-side. Query source availability and expose request
   failures/stale ingestion separately from an empty detection list. These are
   app design choices, not NASA observation or freshness guarantees.
3. Render nominal/high detections by acquisition age; show raw confidence, FRP
   MW, platform, day/night and footprint dimensions on inspection. Display both
   **“observed at”** and **“retrieved at,”** a window/last-success indicator and
   explicit gaps. Reject malformed/future acquisition times and unknown versions.
4. Archive the raw response/hash and processing version. Use source/platform/time/
   pixel identity for deduplication; keep revision/supersession records when NRT
   is replaced. Never sum repeated detections across overpasses as simultaneous
   FRP, assign one event per point, or count RT/NRT copies as new fires.
5. Export the same evidence to CSV/JSON. Keep it in observation context alongside
   the existing METAR view. Existing hazard endpoints, 5×5 probability averaging,
   flame-length thresholds, kW/m statistics, contribution and trigger code remain
   untouched. A later FEDS layer can link modeled histories using region/year/
   fire identifiers and retained merge/split lineage.

Minimum proposed observation record, illustrated using the real first sample:

```json
{
  "data_kind": "satellite_active_fire_observation",
  "metric": "fire_radiative_power",
  "value": 0.17,
  "unit": "MW",
  "latitude": 29.67559,
  "longitude": -98.18747,
  "spatial_support": "detected_pixel_center",
  "scan_km": 0.33,
  "track_km": 0.55,
  "observed_at": "2026-11-02T07:45:00Z",
  "retrieved_at": "2026-11-03T22:57:23.922578Z",
  "published_at": null,
  "provider": "NASA LANCE FIRMS",
  "product": "VJ114IMGTDL_NRT",
  "platform": "NOAA-20",
  "instrument": "VIIRS",
  "source_version": "2.0NRT",
  "confidence_raw": "nominal",
  "daynight": "N",
  "source_url": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_USA_contiguous_and_Hawaii_24h.csv",
  "artifact_sha256": "fa3268823db96b75e7739509a010dcc93936d163bdb9cdb92dd5bd6cdff1582e",
  "event_id": null,
  "revision_status": "near_real_time",
  "annual_probability_available": false
}
```

`published_at` stays null because the feed did not supply a per-detection
publication time. HTTP Last-Modified describes the file snapshot; it must not
fill that field. An acquisition represented to the minute does not imply
subminute timestamp precision. The ingest record also needs query bounds/window,
filter policy/version, normalized confidence, ingestion status and superseded-by
identity where applicable. A hash establishes which bytes were used, not that a
fire caused damage.

For later LST ingestion, additionally preserve product/collection and granule ID,
native CRS/grid/row/column, scale/offset/fill, raw and decoded QA, masks,
actual observation time or composite start/end, processing time, baseline version,
accepted sample count and coverage. Do not assign composite-start midnight as
an instantaneous observation or use retrieval time for climatological matching.

Before shipping the proposed overlay, verify timestamp padding/UTC, full-word and
letter confidence codes, repeated/superseded records, cloud/no-coverage ambiguity,
empty/error/stale responses, and location-change cancellation. For LST, verify
fill/scale/QA and baseline support. These are proposed integration acceptance
checks; no implemented NASA route or browser test is claimed here.

## 8. Evidence limits and verification

- Current NASA documentation and primary papers are cited below. Some Earthdata
  catalog/FAQ pages returned HTTP 403 in the research browser. Product details
  were cross-checked with accessible NASA MODAPS/science-team documentation,
  current NASA Open Data catalog entries, live CMR metadata and actual public
  responses. Failed pages were not treated as evidence of unavailable datasets.
- Exact CMR collection starts were checked live; the historical source granule
  match is archived. FEDS regional documentation is inconsistent, so coverage
  is deliberately conditional. LST standard-product delivery SLAs and service
  quotas not stated above were not independently established.
- Offline sample replay **passed**: four raw response hashes, five selected fire
  rows, source-granule matching, QA decoding, conversions, and the manifest.
  All **41** checked application, test, SQL, and dependency-file hashes matched
  before and after this evaluation. No production calculation changed; no application
  test suite or UI verification was needed for these research-only additions.

## 9. Professor-ready summary

NASA data can improve NDApp's observational context without redefining its hazard
calculations. VIIRS 375 m detections are the strongest first addition: they locate
thermal anomalies at known overpass times and report confidence and FRP. They
do not prove a wildfire perimeter, property burning, or kW/m fireline intensity.
FEDS adds useful modeled event histories, with clustering and coverage limits.
MODIS/VIIRS LST supports regional surface-heat patterns and carefully matched
anomalies; ECOSTRESS adds finer spatial detail with irregular sampling. None
directly measures near-surface air temperature or occupational heat exposure.
The saved San Antonio samples demonstrate both accessible fire observations
and the need to withhold low-quality temperature retrievals. Annual probabilities,
triggers and payouts remain unchanged.

## Sources and citations

All web sources reviewed November 3, 2026. Catalog dates describe the collection,
not guaranteed complete observation coverage. Scientific papers establish methods
and validation scope; current product/version documentation controls ingestion.

- **F1:** [NASA FIRMS Area API: source names, intervals, MAP_KEY and limits](https://firms.modaps.eosdis.nasa.gov/api/area/).
- **F2:** [NASA FIRMS: scope and global delivery latency](https://firms.modaps.eosdis.nasa.gov/).
- **F3:** [NASA MODAPS NOAA-20 VJ114IMGTDL_NRT specification and attribution](https://modaps.modaps.eosdis.nasa.gov/services/about/products/viirs-land-c2-nrt/vj114imgtdl_nrt.html). Dataset DOI: [10.5067/FIRMS/VIIRS/VJ114IMGT_NRT.002](https://doi.org/10.5067/FIRMS/VIIRS/VJ114IMGT_NRT.002).
- **F4:** [VIIRS Active Fire User Guide, science-team PDF](https://viirsland.gsfc.nasa.gov/PDF/VIIRS_activefire_User_Guide.pdf), especially hybrid FRP retrieval (§2.2).
- **F5:** [NASA MODIS Collection 6/C6.1 Fire User Guide](https://www.earthdata.nasa.gov/s3fs-public/2023-09/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf), confidence classes and fire-mask/QA semantics.
- **F6:** [NASA FIRMS Academy: ingestion, fields and timestamps](https://firms.modaps.eosdis.nasa.gov/content/academy/data_ingest/firms_data_ingest.html).
- **F7:** [NASA ARSET 2025 FIRMS Q&A](https://appliedsciences.nasa.gov/sites/default/files/2025-04/FIRMS_Part1_QA.pdf), historical dates and heat-source interpretation.
- **T1:** [NASA/UCI Wildfire Tracking Lab: FEDS overview](https://earth-information-system.github.io/fireatlas/docs/index.html).
- **T2:** [Wildfire Tracking Lab: current NRT access, latency, coverage, and limitations](https://earth-information-system.github.io/fireatlas/docs/nrt.html).
- **T3:** [NASA VEDA OGC Features collections](https://openveda.cloud/api/features/collections).
- **T4:** [NASA ORNL Global Fire Atlas archived dataset, DOI 10.3334/ORNLDAAC/1642](https://doi.org/10.3334/ORNLDAAC/1642); [NASA catalog record with protected-download links](https://catalog.data.gov/dataset/global-fire-atlas-with-characteristics-of-individual-fires-2003-2016).
- **T5:** [NASA MCD64A1 C6.1 Burned Area User Guide](https://www.earthdata.nasa.gov/s3fs-public/2025-04/MCD64_User_Guide_V61.pdf); dataset DOI [10.5067/MODIS/MCD64A1.061](https://doi.org/10.5067/MODIS/MCD64A1.061).
- **T6:** [NASA Earthdata GIS FEDS NOAA-20 layer description](https://gis.earthdata.nasa.gov/portal/home/item.html?id=e2e14a2a640b404c903976b2fb4467f8).
- **T7:** [VEDA NRT large-fire perimeter queryables](https://openveda.cloud/api/features/collections/public.eis_fire_lf_perimeter_nrt/queryables).
- **L1:** [NASA Science: land-surface temperature and its distinction from air temperature](https://science.nasa.gov/earth/earth-observatory/global-maps/land-surface-temperature/).
- **L2:** [NASA MODIS LST product family](https://modis.gsfc.nasa.gov/data/dataprod/mod11.php); MOD11A1 DOI [10.5067/MODIS/MOD11A1.061](https://doi.org/10.5067/MODIS/MOD11A1.061), MYD11A1 DOI [10.5067/MODIS/MYD11A1.061](https://doi.org/10.5067/MODIS/MYD11A1.061).
- **L3:** [NASA science-team MOD11 C6.1 User Guide](https://modis-land.gsfc.nasa.gov/pdf/MOD11_User_Guide_V61.pdf), particularly MOD11A2 scaling, coverage bitmaps and Table 18; [C6 guide](https://lpdaac.usgs.gov/documents/118/MOD11_User_Guide_V6.pdf). MOD11A2 DOI [10.5067/MODIS/MOD11A2.061](https://doi.org/10.5067/MODIS/MOD11A2.061).
- **L4:** [Live NASA CMR MOD11A1 .061 collection query](https://cmr.earthdata.nasa.gov/search/collections.json?short_name=MOD11A1&version=061&provider=LPCLOUD&page_size=1). Substitute `MYD11A1/061`, `MOD11A2/061`, `VNP21A1D/002`, `VNP21A1N/002`, or `ECO_L2T_LSTE/003`; see saved CMR granule for sample-specific evidence.
- **L5:** [NASA Open Data VNP21A1D .002 specification and known issues](https://data.nasa.gov/dataset/viirs-npp-land-surface-temperature-emissivity-daily-l3-global-1km-sin-grid-day-v002-19f02). DOI [10.5067/VIIRS/VNP21A1D.002](https://doi.org/10.5067/VIIRS/VNP21A1D.002).
- **L6:** [NASA VIIRS LST/emissivity algorithm overview](https://viirsland.gsfc.nasa.gov/Products/NASA/LSTESDR.html); [VNP21 user guide (Collection 1 background, not a V002 schema substitute)](https://viirsland.gsfc.nasa.gov/PDF/VNP21_LSTE_user_guide.pdf).
- **L7:** [NASA Open Data ECOSTRESS ECO_L2T_LSTE V003 specification and issues](https://data.nasa.gov/dataset/ecostress-tiled-land-surface-temperature-and-emissivity-instantaneous-l2-global-70-m-v003). DOI [10.5067/ECOSTRESS/ECO_L2T_LSTE.003](https://doi.org/10.5067/ECOSTRESS/ECO_L2T_LSTE.003).
- **L8:** [NASA JPL ECOSTRESS current V002/V003 product documentation index](https://ecostress.jpl.nasa.gov/data/atbds-summary-table).
- **L9:** [NASA JPL ECOSTRESS Cloud ATBD, mission sampling table](https://ecostress.jpl.nasa.gov/downloads/atbd/ECOSTRESS_L2_ATBD_CLOUD_Collection2_rev1.pdf). Revisit is mission background, not a V003 delivery guarantee.
- **A1:** [ORNL TESViS API documentation, request limits, and citation](https://modis.ornl.gov/data/modis_webservice.html). ORNL DAAC (2018), service DOI [10.3334/ORNLDAAC/1600](https://doi.org/10.3334/ORNLDAAC/1600).
- **A2:** [NASA FIRMS Data Availability API](https://firms.modaps.eosdis.nasa.gov/api/data_availability/).
- **A3:** [NASA Earthdata support: restrictions and attribution for FIRMS reuse](https://forum.earthdata.nasa.gov/viewtopic.php?t=5184) (indexed guidance; direct fetch unavailable).
- **A4:** [NASA LANCE: near-real-time versus standard science processing](https://lance.modaps.eosdis.nasa.gov/index.php).
- **A5:** [NASA LAADS example authenticated VIIRS archive download instructions](https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5200/VNP21A1D/2026/054/).
- **A6:** [NASA AppEEARS API: authentication, products, point/area tasks and bundles](https://appeears.earthdatacloud.nasa.gov/api/).
- **A7:** [NASA Science: open Earth-science data policy context](https://science.nasa.gov/mission/landsat/open-data/).
- **P1:** Schroeder, W., Oliva, P., Giglio, L., & Csiszar, I. A. (2014). *The New VIIRS 375 m active fire detection data product: Algorithm description and initial assessment*. Remote Sensing of Environment, 143, 85–96. [DOI 10.1016/j.rse.2013.12.008](https://doi.org/10.1016/j.rse.2013.12.008); [NASA-hosted paper](https://directreadout.sci.gsfc.nasa.gov/publications_documents/Schroeder_et_al_2014b_RSE.pdf).
- **P2:** Giglio, L., Schroeder, W., & Justice, C. O. (2016). *The collection 6 MODIS active fire detection algorithm and fire products*. Remote Sensing of Environment, 178, 31–41. [Primary paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC6110110/), DOI 10.1016/j.rse.2016.02.054.
- **P3:** Chen, Y., et al. (2022). *California wildfire spread derived using VIIRS satellite observations and an object-based tracking system*. Scientific Data, 9, 249. [Primary paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC9151742/), DOI 10.1038/s41597-022-01343-0.
- **P4:** Wooster, M. J., Roberts, G., Perry, G. L. W., & Kaufman, Y. J. (2005). *Retrieval of biomass combustion rates and totals from fire radiative power observations: FRP derivation and calibration relationships between biomass consumption and fire radiative energy release*. JGR Atmospheres, 110, D24311. [DOI 10.1029/2005JD006318](https://doi.org/10.1029/2005JD006318).
- **P5:** Hulley, G. C., Malakar, N. K., Islam, T., & Freepartner, R. J. (2018). *NASA's MODIS and VIIRS Land Surface Temperature and Emissivity Products: A Long-Term and Consistent Earth System Data Record*. IEEE JSTARS, 11(2), 522–535. [NASA science-team abstract](https://modis.gsfc.nasa.gov/sci_team/pubs/abstract_new.php?id=27878), DOI 10.1109/JSTARS.2017.2779330.
- **P6:** Andela, N., et al. (2019). *The Global Fire Atlas of individual fire size, duration, speed and direction*. Earth System Science Data, 11, 529–552. [Primary paper](https://essd.copernicus.org/articles/11/529/2019/), DOI 10.5194/essd-11-529-2019. [Later extension described in State of Wildfires 2023–2024](https://doi.org/10.5194/essd-16-3601-2024).
- **P7:** Giglio, L., Boschetti, L., Roy, D. P., Humber, M. L., & Justice, C. O. (2018). *The Collection 6 MODIS burned area mapping algorithm and product*. Remote Sensing of Environment, 217, 72–85. [Primary paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC6136150/), DOI 10.1016/j.rse.2018.08.005.

[F1]: https://firms.modaps.eosdis.nasa.gov/api/area/
[F2]: https://firms.modaps.eosdis.nasa.gov/
[F3]: https://modaps.modaps.eosdis.nasa.gov/services/about/products/viirs-land-c2-nrt/vj114imgtdl_nrt.html
[F4]: https://viirsland.gsfc.nasa.gov/PDF/VIIRS_activefire_User_Guide.pdf
[F5]: https://www.earthdata.nasa.gov/s3fs-public/2023-09/MODIS_C6_C6.1_Fire_User_Guide_1.0.pdf
[F6]: https://firms.modaps.eosdis.nasa.gov/content/academy/data_ingest/firms_data_ingest.html
[F7]: https://appliedsciences.nasa.gov/sites/default/files/2025-04/FIRMS_Part1_QA.pdf
[T1]: https://earth-information-system.github.io/fireatlas/docs/index.html
[T2]: https://earth-information-system.github.io/fireatlas/docs/nrt.html
[T3]: https://openveda.cloud/api/features/collections
[T4]: https://doi.org/10.3334/ORNLDAAC/1642
[T5]: https://www.earthdata.nasa.gov/s3fs-public/2025-04/MCD64_User_Guide_V61.pdf
[T6]: https://gis.earthdata.nasa.gov/portal/home/item.html?id=e2e14a2a640b404c903976b2fb4467f8
[T7]: https://openveda.cloud/api/features/collections/public.eis_fire_lf_perimeter_nrt/queryables
[L1]: https://science.nasa.gov/earth/earth-observatory/global-maps/land-surface-temperature/
[L2]: https://modis.gsfc.nasa.gov/data/dataprod/mod11.php
[L3]: https://modis-land.gsfc.nasa.gov/pdf/MOD11_User_Guide_V61.pdf
[L4]: https://cmr.earthdata.nasa.gov/search/collections.json?short_name=MOD11A1&version=061&provider=LPCLOUD&page_size=1
[L5]: https://data.nasa.gov/dataset/viirs-npp-land-surface-temperature-emissivity-daily-l3-global-1km-sin-grid-day-v002-19f02
[L6]: https://viirsland.gsfc.nasa.gov/Products/NASA/LSTESDR.html
[L7]: https://data.nasa.gov/dataset/ecostress-tiled-land-surface-temperature-and-emissivity-instantaneous-l2-global-70-m-v003
[L8]: https://ecostress.jpl.nasa.gov/data/atbds-summary-table
[L9]: https://ecostress.jpl.nasa.gov/downloads/atbd/ECOSTRESS_L2_ATBD_CLOUD_Collection2_rev1.pdf
[A1]: https://modis.ornl.gov/data/modis_webservice.html
[A2]: https://firms.modaps.eosdis.nasa.gov/api/data_availability/
[A3]: https://forum.earthdata.nasa.gov/viewtopic.php?t=5184
[A4]: https://lance.modaps.eosdis.nasa.gov/index.php
[A5]: https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5200/VNP21A1D/2026/054/
[A6]: https://appeears.earthdatacloud.nasa.gov/api/
[A7]: https://science.nasa.gov/mission/landsat/open-data/
[P1]: https://directreadout.sci.gsfc.nasa.gov/publications_documents/Schroeder_et_al_2014b_RSE.pdf
[P2]: https://pmc.ncbi.nlm.nih.gov/articles/PMC6110110/
[P3]: https://pmc.ncbi.nlm.nih.gov/articles/PMC9151742/
[P4]: https://doi.org/10.1029/2005JD006318
[P5]: https://modis.gsfc.nasa.gov/sci_team/pubs/abstract_new.php?id=27878
[P6]: https://essd.copernicus.org/articles/11/529/2019/
[P7]: https://pmc.ncbi.nlm.nih.gov/articles/PMC6136150/
