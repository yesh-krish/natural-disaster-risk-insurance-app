# NDApp disaster viewer

## Trigger probability UI

The top panel shares the existing disaster selector and location selection.
After `/api/disaster` returns, the trigger dropdown lists its finite,
nonnegative source severity levels with valid frequencies from 0 to 100%.
Choosing a level displays its unchanged source frequency. No custom-level
interpolation or extrapolation is performed. Coincident severity values with
different frequencies display the supplied frequency range and an explanation,
rather than an arbitrary single probability.

The result card identifies the hazard, measurement and unit, coordinates,
government source and one-year interpretation. Earthquake includes site class;
wildfire is labeled an estimated annual probability; cyclone is labeled an
observed annual frequency, with its future-probability limitation. Genuine source
zeros remain visible with context. Missing or invalid points never become zeros.

Location, hazard and site-class changes immediately clear both the trigger
options and the previous result. Requests use the existing abort/version guards.
Unavailable coverage and service errors disable the dropdown and explain the
next action. Flood now also loads `/api/disaster` for its trigger choices; its
existing depth/damage table remains separate. Editing that table or loading its
illustrative example cannot change the trigger probability.

Verify the pure source-to-trigger contract with
`node tests/test_hazard_trigger.cjs`.

## Choose most relevant

The automatic dropdown option calls `/api/disaster?peril=auto&lat=...&lon=...`.
It remains selected after resolution so every new location is evaluated again.
The response contains the resolved `peril`, its unchanged government curve,
and `selection` metadata explaining the result.

The official FEMA National Risk Index Counties service is queried with a WGS84
point intersection. Supported hazards are ranked by their county total expected
annual losses, a shared dollar unit: `ERQK_EALT` (earthquake), `HRCN_EALT`
(hurricane), `WFIR_EALT` (wildfire), and the sum of known `IFLD_EALT` and
`CFLD_EALT` components (inland and coastal flooding). Null/sentinel values remain
missing; zero is distinct. The returned county, model version and response
SHA256 are retained. This ranking provides regional context, not parcel-level
risk or an insurance premium.

Candidates are checked in descending order. A usable automatic curve must have
at least one positive finite severity/frequency point. Manual selections still
display valid modeled/observed zero frequencies. If flood ranks first among the
remaining candidates but has no usable local curve, and inland losses dominate
its known components, rainfall is tried as related context. It is never labeled
as an inundation-depth estimate or as a standalone FEMA NRI hazard category.
Coastal flooding is not mapped to rainfall this way.

Each checked candidate and the reason for its availability/skipping are included
in `selection.checks`. County outages, missing rankings or exhausted candidates
use a clearly labeled available-data fallback in this fixed order: rainfall,
earthquake, wildfire, cyclone, flood. This fallback claims only data availability.
If all curves are unavailable/zero, an explicit unavailable result is returned.
Manual selection always remains available. Neither blank flood depths nor zero
observed cyclone years establish no future risk.

The browser uses five separate disaster selections. Selecting a disaster
re-queries the selected location; selecting a location queries the selected
disaster. Previous graphs/details clear immediately, and aborted or superseded
responses cannot overwrite a newer selection. Government failures and missing
coverage show an unavailable state. Flood retains its separate building-damage
controls; these controls are hidden for other disasters.

## Graph conventions

X is annual exceedance probability or observed annual exceedance frequency,
in percent, increasing left to right. Y is severity in the selected metric.
The X scale is logarithmic when every probability is positive, otherwise linear
so source zeros remain visible. Missing severity is omitted. Lines guide the
eye between supplied points; NDApp does not infer new scenario probabilities
from these lines. Flood inputs/CSV retain source feet; the graph converts to meters.

## Sources and calculations

Earthquake: discover a region-specific static NSHM model using the
[USGS model catalog](https://earthquake.usgs.gov/ws/nshmp/hazard/static/nshms),
then request its `/hazard` endpoint with latitude, longitude, `imt=PGA`,
`siteClass`, and `format=JSON`. Convert USGS intensity g to %g by multiplying
by 100. Convert annual exceedance rate λ to annual probability with
`-expm1(-λ)`. Site class BC is the initial assumption, exposed in the UI.
Supported regions are CONUS, Alaska, Hawaii and Puerto Rico/USVI, subject to
the provider's actual grid coverage.

Rainfall: request NOAA's
[Atlas 14 point-frequency service](https://hdsc.nws.noaa.gov/hdsc/pfds/)
CSV with `data=depth`, `units=metric`, `series=ams`, latitude and longitude.
Read only the `24-hr:` row and the AEP headings. The annual-maximum series
supports AEP `1/T`; no Poisson conversion or wind metric is used.

Flood: use installed TWDB existing-conditions scenario-5 depth rasters and
the [FEMA NFHL map](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer).
The graph uses 20%, 10%, 1%, 0.2% annual-chance depths, converting feet to meters
using 0.3048. FEMA zone attributes are context and never substitute for depths.
See the main README for downloads, coverage and Hazus damage assumptions.

Cyclone: discover current Atlantic and northeast/central Pacific HURDAT2 files
from [NOAA NHC's data page](https://www.nhc.noaa.gov/data/). For complete calendar
years from 2004 through the published end year, test the selected point against
the reported 34, 50 and 64 knot wind radii. Distance uses nautical miles;
the initial bearing determines the NE, SE, SW or NW quadrant. Count each year
at most once per threshold, regardless of track records or number of storms:
`frequency = hit_years / complete_years`. These are one-minute sustained-wind
footprints, not local peak gusts. Missing radii and passages between records
are not reconstructed. The result is a sampled historical frequency, not a
calibrated future annual probability. Zero observed hits does not mean zero risk.

Fire: sample original 270 m quantitative rasters from the
[USFS FSim third-edition archive](https://www.fs.usda.gov/rds/archive/Catalog/RDS-2016-0034-3),
with CRS transformation and NoData masking. BP is an annual burn-probability
fraction; FLP5 and FLP6 are conditional flame-length-class probabilities for
8–12 ft and 12+ ft. Plot only the two supported thresholds:

- 8 ft = 2.4384 m: estimated annual probability `BP * (FLP5 + FLP6)`.
- 12 ft = 3.6576 m: estimated annual probability `BP * FLP6`.

This factorization is a research approximation to annual exceedance, with the
model's LF2020 landscape conditions. WHP is a separate categorical context
from the USFS public ImageServer. Its class is never interpreted as AEP.
Rescaled visualization pixels from the WRC web map are never used as quantitative
probabilities. Daily FWI and live payout execution are outside this viewer.

## API

`GET /api/disaster?peril=earthquake&lat=29.4241&lon=-98.4936&site_class=BC`

Allowed perils: `flood`, `earthquake`, `wildfire`, `cyclone`, `rainfall`.
The `auto` option returns one of those perils with `selection` metadata, or
`peril: auto` with `status: unavailable` if none can provide a usable curve.
Coordinates and site class are validated. Successful responses contain
`peril`, `latitude`, `longitude`, `status`, `points`, `metric`, `unit`,
`frequency_label`, `details`, `notes`, `source_name`, and `source_url`.
Each point contains `frequency_pct` and `severity`. Coverage errors return
`status: unavailable`, `reason` and no fabricated points; service failures
return HTTP 502. The older `/api/hazards` pricing research bundle is separate
and is not the route consumed by the disaster selector.
