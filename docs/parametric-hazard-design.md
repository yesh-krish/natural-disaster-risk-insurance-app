# NDApp parametric hazard architecture

This design extends NDApp's Python/TWDB flood research prototype. `hazard_engine.py`
is executable research code; `database/hazard_schema.sql` is a PostgreSQL/PostGIS
blueprint. Only USGS static PGA curves and the existing installed TWDB depth
rasters have coordinate adapters. Rain/wind and wildfire need data ingestion and
calibration before they can return location quotes. No payment provider, sensor
poller, production database, or live settlement worker is installed by this change.

## 1. Common probability and contract model

Let `s=(latitude,longitude)`, `I_e(s)` be event intensity, and `M_y(s)` the maximum
intensity during a year. Keep these quantities distinct:

\[
\lambda_s(x)=E[\#\{e:I_e(s)\ge x\}\text{ per year}],\qquad
AEP_s(x)=P(M_y(s)\ge x).
\]

Under a stationary Poisson occurrence assumption:

\[
AEP_s(x)=1-e^{-\lambda_s(x)},\quad
P_T(x)=1-e^{-T\lambda_s(x)},\quad
\lambda_s(x)=-\log(1-AEP_s(x)).
\]

Use `-expm1(-lambda)` and `-log1p(-p)` for numerical stability. For a nonstationary
model replace `T*lambda` with the integrated hazard `integral lambda(t,x) dt`.
An annual-max return period uses `p=1/T_return`; an event mean recurrence interval
uses `lambda=1/T_return`. These definitions are close for rare events but differ
materially for common ones. A 1% annual-chance flood has AEP **0.01**, not
`1-exp(-0.01)`. A USGS annual rate of 0.01 has AEP **0.00995016625**.

Coordinates select a covered raster cell, agency grid interpolation, or calibrated
site model. Coordinates alone do not estimate frequency. Store model version,
measurement definition, units, calibration years, scenario, sampling method,
uncertainty, and source artifacts. Missing coverage is `unavailable`, never zero.
The schema stores annual rates in `1/year` and probabilities as fractions in `[0,1]`.

### Benchmark pricing implemented here

The implemented contract is **one year, annual maximum, cumulative top-up**. It
has nested thresholds `x_j` and increasing cumulative payouts `b_j`, with `b_0=0`.
It pays at most `b_m` over the term. If a later event reaches a higher tier, it pays
the difference between the newly earned tier and the amount already paid/reserved.

\[
Y=\sum_{j=1}^{m}(b_j-b_{j-1})\mathbf1\{M_y\text{ reaches tier }j\},
\quad E[Y]=\sum_j(b_j-b_{j-1})AEP_j.
\]

For a single fixed benefit `B` at threshold `x`, `E[Y]=B*AEP(x)`. For a continuous
nondecreasing payout function with zero payout at zero, `E[Y]=integral S_M(x) db(x)`.
Use a specified numerical integration/tail convention if implementing that product.
The tier engine avoids inventing payouts beyond a modeled curve.

\[
\pi_{benchmark}=\frac{(1+k)E[Y]+c}{1-e}
\]

`k` is an explicit risk load on expected payouts, `c` a fixed expense, and `e` an
expense ratio. This is a transparent benchmark, not a capital or reinsurance model.
For portfolio pricing estimate correlated policy payouts with shared event simulations;
e.g. use a cost-of-capital charge on a specified tail capital measure. Neither an
independence assumption nor a percentage load estimates catastrophe accumulation.
Freeze the accepted model, terms, assignments, and premium at binding; use refreshed
hazards for new quotes/renewals unless an agreed repricing rule says otherwise.

For an **uncapped per-event** product the correct expected payout is
`sum_e annual_rate_e * payout(I_e)`, or `B*lambda` for a fixed event benefit.
Annual probability must not replace event frequency there. For aggregate limits,
deductibles, reinstatements, or nonnested schedules, use event-year simulation of
the actual payment rules; this implementation deliberately prices nested annual
maximum schedules. Do not scale annual-max probabilities linearly for partial years.
For stationary Poisson occurrence `p_T=1-(1-p_1)^T`; seasonality requires integrating
the seasonal hazard instead. Annual-max empirical models do not automatically justify
that transformation.

## 2. Earthquake

**Intensity:** site PGA in `%g`; `PGA_pct_g=100*PGA_g`. If a sensor returns SI acceleration,
`PGA_pct_g=100*acceleration_m_s2/9.80665`. Earthquake magnitude is a source property,
not insured-site shaking.

The probabilistic seismic hazard model conceptually integrates earthquake source
rates and conditional ground-motion exceedance:

\[
\lambda_s(x)=\sum_k\int\int \nu_k(m,r)
P(PGA(s)\ge x\mid m,r,Vs30,\ldots)\,dm\,dr.
\]

Use USGS's published result, retaining site class/Vs30, model region and revision,
ground-motion component and site corrections. Interpolate positive nodes in
`log(intensity),log(annual_rate)`; convert the interpolated rate to AEP afterward.
Do not interpolate percentages labeled as annual rates, extrapolate unsupported
tails, substitute building-code design values for a full curve, or silently change models.

Verified on **2026-10-11**:

| Purpose | Mapping |
|---|---|
| Discover installed static NSHMs | `GET https://earthquake.usgs.gov/ws/nshmp/hazard/static/nshms`; select an explicit `response[].id` and its relative `url` |
| Model contract | `GET https://earthquake.usgs.gov{response[].url}/swagger`; currently YAML, not JSON |
| Site PGA | `GET https://earthquake.usgs.gov{response[].url}/hazard?latitude={lat}&longitude={lon}&siteClass={class}&imt=PGA&format=JSON` |
| Normalize | `response.data.xs` = g; multiply by 100. `response.data.ys` = annual exceedance rate. Validate `response.metadata` and returned site |
| Dynamic model discovery | `GET https://earthquake.usgs.gov/ws/nshmp/hazard/dynamic/nshms`; use that model's Swagger for continuous `Vs30` and supported IMTs; not implemented by the static adapter |
| Event discovery | `GET https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=...&endtime=...&minlatitude=...&maxlatitude=...&minlongitude=...&maxlongitude=...` |
| Event PGA | Follow a ComCat event's `properties.detail`; select its preferred ShakeMap product, then its `contents["download/grid.xml"].url` where available. Sample insured-site PGA, preserving product version and finality |

The returned model catalog includes `STATIC_CONUS_2023_R2` with URL
`/ws/nshmp/conus-2023.R2/static`. Its static service accepts **siteClass**, not a
continuous Vs30 parameter. A San Antonio `(29.4241,-98.4936)`, class BC request
returned 20 curve nodes; the first was `(0.233 %g, 0.0125044519 /year)`.
This response is saved in `examples/san_antonio_hazards.json` with source hash.
That class is an explicit research assumption, not a site investigation.

Use a contract-approved final ShakeMap site PGA or an authenticated site sensor
with the same measurement definition. ShakeMap `grid.xml` declares PGA units
`pctg`; it is an estimated shaking field and can be revised. Pin product acceptance
rules and component compatibility before binding. [USGS service guide](https://ghsc.code-pages.usgs.gov/nshmp/nshmp-apps/apps/),
[ShakeMap product documentation](https://ghsc.code-pages.usgs.gov/esi/shakemap/docs2020/manual4_0/ug_products.html),
[ComCat API](https://earthquake.usgs.gov/fdsnws/event/1/).

## 3. Rainfall and wind: a same-storm hazard surface

For each declustered storm at the insured site:

\[
R_e(s)=\max_t\int_{t-24h}^{t}rain(s,u)du,\qquad
W_e(s)=\max_t gust(s,t).
\]

Rain is an accumulation in **mm**, not mm/hour. Wind is a gust in **knots** with
specified averaging duration, measurement height and exposure (e.g. approved
3-second, 10-meter gust). `kn=m/s * 1.94384449244`, `mph=kn * 1.15077944802`.
The maxima can occur at different times **within the same canonical storm**.
Do not pair the year's strongest wind with rainfall from an unrelated event.
Overlapping rolling rain windows are not independent storm observations.

An event-level copula can model dependence. For marginal CDFs `F_R,F_W` and copula `C`:

\[
S_{AND}(r,w)=1-F_R(r)-F_W(w)+C(F_R(r),F_W(w)),
\quad S_{OR}(r,w)=1-C(F_R(r),F_W(w)).
\]

For storm arrival rate `nu`, `lambda_AND=nu*S_AND` and
`AEP_AND=1-exp(-lambda_AND)` (similarly OR). These CDFs are **conditional on a
storm event**, not independent annual maxima. GEV annual-max marginals alone
cannot recover the same-storm surface. Fit event-tail models (e.g. GPD above a
declustering threshold) and an appropriate tail-dependent copula or use a
validated synthetic catalog. Validate AND/OR frequencies and out-of-sample tail
behavior; avoid assuming `P(R)*P(W)`.

The executable catalog method uses paired site-event intensities:

\[
\lambda(r,w)=\sum_e q_e\mathbf1(R_e\ge r\;AND\;W_e\ge w).
\]

For complete historical exposure `H` years, each observed independent storm has
weight `q_e=1/H`. Synthetic events have documented simulation weights. Separate
hurricane and convective populations when fitting, then combine rates for
nonoverlapping event classifications. Bootstrap by storm/year, preserve missing
data years, and calibrate changes in climate and observation practice. An empty
rare tail is unsupported, not proof of zero risk; `StormCatalog` refuses such quotes.
Its `surface()` exports null AEP cells for unsupported thresholds.

| Purpose | Mapping |
|---|---|
| Rainfall frequency marginal | NOAA Atlas 14 PFDS point products/downloadable grids; select 24-hour, units, volume/version and AMS vs PDS basis. These do not provide a joint wind surface |
| Historical gust/rain pairs | NCEI ISD Global Hourly; `https://www.ncei.noaa.gov/access/services/data/v1?dataset=global-hourly&stations={station_id}&startDate=...&endDate=...&format=json&includeAttributes=true`; parse dataset quality flags and encoded gust/accumulation fields, including durations and scale factors |
| Live station discovery | `GET https://api.weather.gov/points/{lat},{lon}`; follow `properties.observationStations` and pin a representative approved station |
| Live gust observation | `GET https://api.weather.gov/stations/{station_id}/observations`; read `properties.windGust.value`, its `unitCode`, timestamp and quality. Missing gust is null |
| Live 24-hour rain | NOAA MRMS gauge-corrected QPE 24-hour GRIB2 products from `https://mrms.ncep.noaa.gov/data/`; retain issue time, product code/version and quality. Sample the site or specified footprint and compute event maximum rolling 24-hour total |
| Storm identity | HURDAT2/IBTrACS hurricane tracks and a documented convective declustering process |

The NWS observations endpoint is a live weather source, not a calibrated national
hazard surface. Its 1/3/6-hour precipitation fields must not be mistaken for a
complete rolling 24-hour maximum; require continuous intervals and handle overlaps,
resets and gaps. IBTrACS track winds are maximum **sustained** winds and require a
site wind-field and gust model to yield local peak gusts. Headers for NWS must include
an identifying User-Agent. [NWS API](https://www.weather.gov/documentation/services-web-api),
[NCEI ISD](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database),
[NCEI API](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation),
[Atlas 14 FAQs](https://www.weather.gov/owp/hdsc_faqs),
[MRMS](https://inside.nssl.noaa.gov/mrms/),
[IBTrACS documentation](https://www.ncei.noaa.gov/sites/default/files/2025-09/IBTrACS_v04r01_column_documentation.pdf).

## 4. Flood: water level to insured-site inundation

For compatible water-surface and terrain elevations in a common vertical datum:

\[
D_e(s)=\max(0,H_e(s)-z_{ground}(s)),\qquad
AEP_D(d)=P(H_{max}(s)\ge z_{ground}(s)+d),\quad d>0.
\]

Specify depth above ground versus above finished floor; they are different contracts.
Store horizontal CRS, vertical datum, elevation source/accuracy and any datum
conversion. A gauge's height above its local datum is not a property's water-surface
elevation. Hydraulic connectivity, levees, drainage and flow gradients matter.

**River:** obtain qualified annual peak flow and approved Bulletin 17C/PeakFQ
frequency quantiles `Q_p`; map through an appropriate hydraulic model
`H_p(s)=HydraulicModel(Q_p, reach, terrain, structures)`, then `D_p(s)`.
Use a current rating curve to translate sensor stage/discharge where valid, then
the approved reach-to-site transfer. Select gauges by connected river reach/basin
and representativeness, not simply nearest Euclidean distance. Live discharge
does not itself provide an AEP curve. [USGS PeakFQ](https://www.usgs.gov/tools/peakfq).

**Coastal:** use tide-gauge extreme-water-level annual probabilities and a coastal
inundation model. Sea-level scenarios, tidal datum transformation, surge, waves and
connectivity must be defined. Do not add independent rainfall/river/surge
probabilities for the same hurricane; use a combined event model or dependence
model. For a combined-peril raster such as NDApp's TWDB source, do not add a second
coastal hazard curve to it. [NOAA coastal inundation data](https://coast.noaa.gov/digitalcoast/topics/coastal-inundation.html).

| Purpose | Mapping |
|---|---|
| Regulatory context | `https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DEPTH&f=json` |
| FEMA frequency-depth data | Study-specific FIS profiles, water-surface/depth grids and terrain; inspect their event probabilities, datum and coverage. NFHL zone attributes alone do not yield a full frequency-depth curve |
| USGS gauge discovery | `https://api.waterdata.usgs.gov/ogcapi/v1/collections/monitoring-locations/items?bbox={west},{south},{east},{north}&f=json`; use metadata and network matching |
| Latest gauge measurements | `https://api.waterdata.usgs.gov/ogcapi/v1/collections/latest-continuous/items?monitoring_location_id=USGS-{site_id}&parameter_code=00065&f=json`; 00065 is gage height, 00060 discharge; verify returned units, qualifiers and time series |
| History and annual peaks | `/collections/continuous/items` and `/collections/peaks/items`; follow collection metadata/queryables and pagination rather than assuming legacy NWIS JSON shapes |
| Coastal observations | `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=water_level&station={id}&date=latest&datum={approved_datum}&units=metric&time_zone=gmt&format=json&application=NDApp`; retain quality flags |
| Coastal frequency model | CO-OPS extreme water levels / Derived Product API and station metadata/datums; select annual probability output and supported station. This requires coastal hydraulic transfer to the property |
| Existing NDApp adapter | Local TWDB scenario 5 combined-peril depths for 20%, 10%, 1%, 0.2% annual chance; feet to meters via 0.3048 |

The USGS Water Data **V1** API is current; the agency's migration guide replaces
legacy WaterServices `/iv` with modern continuous/latest-continuous collections.
Review collection metadata because parameter support differs across collections.
[USGS migration guide](https://api.waterdata.usgs.gov/docs/ogcapi/migration/),
[V1 release](https://waterdata.usgs.gov/blog/api-v1-release/),
[CO-OPS API](https://api.tidesandcurrents.noaa.gov/api/prod/).

NDApp leaves TWDB zero/NoData cells blank. The new adapter requires all four
positive sampled depths and an ordered curve; equal depths or decreasing depth
with rarer events need model review, not arbitrary sorting/smoothing to make a quote.
Interpolate log AEP against linear depth only within supported nodes. Do not claim
extrapolated rare tails or nationwide coverage. FEMA BFE is a water-surface level,
and depth fields can have restricted zone-specific meanings/sentinel values; inspect
the layer/study metadata before use. [FEMA NFHL](https://www.fema.gov/flood-maps/national-flood-hazard-layer),
[TWDB source](https://twdb-flood-planning-resources-twdb.hub.arcgis.com/pages/cursory-floodplain-data-2025).

## 5. Wildfire: probability, intensity and weather are separate

**WHP** is a relative potential for difficult-to-control wildfire, not an annual
probability or live intensity. **BP** estimates the annual chance a pixel burns;
it is a frequency input. WHP2023 combines modeled likelihood/intensity, fuels and
historical occurrence; it is not a sensor feed. [USFS WHP](https://research.fs.usda.gov/firelab/products/dataandtools/wildfire-hazard-potential).

Use annual probability of burning together with a **compatible conditional
flame-length distribution** for a physical intensity curve:

\[
AEP_L(\ell;s)=BP(s)P(L_{annual\ max}\ge\ell\mid s\text{ burns in year}).
\]

If a product instead reports per-fire conditional flame lengths, use its event
catalog and rates to calculate the annual maximum; do not silently assume an
annual-max interpretation. Conditional mean flame length alone is insufficient
to produce a survival curve. Spatially interpolating BP/class rasters also needs
a model-specific rule; categorical WHP should normally use an identified cell/class.

An event-rate formulation, when validated as appropriate, is
`lambda_L(l)=nu_burn * P(L_e>=l | site burn event)` with
`nu_burn=-log(1-BP)` under the chosen Poisson assumption. The annual-max product
formula and this event-rate formula have different conditional definitions; keep
the selected one in model metadata.

WHP, BP, conditional intensity rasters and historical burns are separate inputs.
WRC downloads supply BP and flame-length-related layers; inspect the release's
metadata to distinguish conditional exceedance probabilities from unconditional
probabilities and convert feet to meters where needed.
[WRC download definitions](https://wildfirerisk.org/download/).

Use Canadian FWI components consistently: weather and previous state determine
FFMC, DMC and DC; `ISI=f(FFMC,wind)`, `BUI=g(DMC,DC)`, `FWI=h(ISI,BUI)`.
FWI is not a simple linear fuel-moisture-plus-wind score. Use a validated CFFDRS
implementation with temperature, relative humidity, wind, precipitation, local-noon
timing, startup/overwinter handling and prior-day states. It is distinct from the
Fosberg Fire Weather Index and US NFDRS indexes.
[Natural Resources Canada FWI](https://prod-natural-resources.azure.cloud.nrcan-rncan.gc.ca/forests-forestry/wildland-fires/canada-fire-weather-index-system).

For a daily weather-conditioned model, fit coefficients to historical site burn
outcomes, weather, fuels, ignition/spread exposure and seasonality. One baseline-
preserving allocation over a full modeled year is:

\[
w_d=\exp(\beta^Tz_d+season_d+spatial_s),\quad
h_d=\frac{-\log(1-BP)w_d}{\sum_{u\in year}w_u},\quad
p_d=1-e^{-h_d}.
\]

Thus `1-product_d(1-p_d)=BP`. Calibrate weather effects and forecasts separately;
normalization over only the next few days would misallocate the whole annual BP.
This formula preserves the baseline annual BP rather than predicting a new annual
BP for an unusually dry year. For climate-conditioned repricing, fit and validate
a separate annual integrated hazard model. Propagate parameter and climate
uncertainty. Multiplying BP by today's raw FWI is not a probability model.

| Purpose | Mapping |
|---|---|
| WHP contextual covariate | USFS WHP product page -> versioned raster downloads or its advertised ArcGIS image service; sample in native CRS, retain continuous value/class and resolution |
| BP and intensity | WRC All Lands Research Data Archive downloads linked from `https://wildfirerisk.org/download/`; ingest compatible BP and conditional flame-length exceedance layers into raster/object storage |
| Daily FWI inputs | Approved station/reanalysis weather history and forecast fields; run the validated FWI state equations, or ingest a clearly identified agency FWI product |
| Actual burn evidence | Contract-approved final fire perimeter/sensor product linked to a canonical incident; apply the specified insured footprint intersection/transfer and revision rule |

For a simpler first wildfire product, a **confirmed burn of the insured footprint**
can be a binary trigger priced using a matching footprint burn model. A pixel BP
is not automatically the probability of any burn within a buffer or footprint:
cell burns are spatially correlated. That alternate binary product needs its own
calibration/contract and is not implemented by the flame-length engine here.
For the implemented flame-length tiers, real observations or an explicitly accepted
incident intensity model are necessary. A perimeter, active-fire detection, WHP or
high FWI alone does not establish the flame-length threshold at the property.

## 6. Data contract, storage and trigger processing

`schemas/hazard-location.schema.json` is Draft 2020-12. It requires each of the four
perils exactly once, discriminates unavailable data from curves/surfaces, records
measurement definitions and provenance, and separates annual rates from annual
probabilities. JSON Schema checks shape, unit mapping and ranges. Ingest must also
check finite numbers, node monotonicity, AEP/frequency consistency, compatible
definitions and source versions. Null hashes are allowed for exploratory local
rasters; compute and record immutable artifact hashes before approving a model.
Confidence interval and scenario metadata can live in `assumptions`; production
storage has an explicit uncertainty field. Never mistake climate/model quantiles
for an intensity exceedance distribution.

`database/hazard_schema.sql` separates locations, raw source artifacts, immutable
metric definitions, hazard snapshots, curve/surface nodes, weighted events,
monitoring assignments, observations/revisions, policy terms, quotes, annual
balances, trigger decisions and an idempotent payment outbox. PostGIS supports
coverage/footprint matching; object storage holds large rasters/GRIBs and raw
responses. Regional flood transfer belongs in the monitoring assignment.

The application flow is:

1. **Ingest/model:** download, verify coverage and source metadata, normalize units,
   fit/calibrate offline, persist immutable artifacts and models; approve a snapshot.
2. **Quote:** coordinate -> covered model -> tier AEP -> expected contractual payout
   -> stated loads -> benchmark quote. Missing coverage or unsupported tail stops it.
3. **Bind:** pin model, annual term, `>=` comparator, schedule, AND/OR, site definition,
   approved assignments, event identity/window rule and finality rule.
4. **Monitor:** trusted worker ingests sensor/agency records, authenticates source,
   assigns canonical event, normalizes measurements, checks completeness and picks
   the accepted revision. It passes resolved observations to `evaluate_trigger`.
5. **Evaluate:** same location/peril/event, accepted source/definition/unit, valid
   time window, complete 24-hour rain, accepted quality and finalized evidence must
   all hold. Missing/provisional/mismatched data returns `triggered=false` with
   `status=insufficient_data`; it is not evidence that the event stayed below threshold.
6. **Settle:** lock the policy-year balance, evaluate with paid **or reserved** total,
   append the decision and reserve the top-up in one transaction; enqueue a payment
   with a unique key. Durable worker retries/reconciles that same key. Concurrent
   events and changed provider revisions must not pay the tier twice.

The trigger code is a pure decision function, not authentication. An artifact hash
or `finalized=true` supplied by an arbitrary client proves nothing; those fields
must come from stored evidence created by the trusted ingest worker. Do not expose
this evaluator directly as a payment API accepting user-provided readings or balances.
Store settlements as integer currency minor units with a currency-specific rounding
rule; benchmark expectations may be fractional. Research code accepts numeric
amounts, so its outputs require authoritative monetary conversion before settlement.

Policy evaluation checks insured observation windows; monitoring workers can process
late final evidence after expiry under the policy's reporting rules. Finality and
maximum publication age are configurable ingestion/contract rules, not universal
agency guarantees. Accepted provisional-trigger products need a separately priced
revision/correction rule. Likewise, a modeled-intensity contract must price using
a hazard/model definition consistent with that observation product, including its
basis risk and measurement uncertainty.

## 7. Run and integrate with NDApp

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe examples/hazard_demo.py
.\.venv\Scripts\python.exe verify_hazard_api.py --live-usgs
.\.venv\Scripts\python.exe server.py
```

Coordinate response using installed flood data:
`GET /api/hazards?lat=29.4241&lon=-98.4936`.

Include an explicit seismic model/site class to fetch its curve:
`GET /api/hazards?lat=29.4241&lon=-98.4936&earthquake_model=STATIC_CONUS_2023_R2&site_class=BC`.
The endpoint returns a research bundle, not a binding premium. Agency/network
failure returns an error, and unsupported models/coverage return unavailable data.
The static catalog is discovered at request time in this prototype; production
should cache catalogs/curves with versioned keys and preserve bound snapshots.
The current Leaflet flood UI continues to use `/api/depths`.

Coordinate-based pricing in Python:

```python
from hazard_engine import Tier, quote_at
from hazard_providers import USGSPGAProvider

quote = quote_at(
    29.4241, -98.4936,
    USGSPGAProvider("STATIC_CONUS_2023_R2", "BC"),
    [Tier({"pga_pct_g": 10}, 10_000),
     Tier({"pga_pct_g": 30}, 30_000)],
    annual_limit=30_000,
    fixed_expense=50, risk_load=0.20, expense_ratio=0.15,
)
```

These amounts and loads are user-specified research assumptions. The demo uses
invented frequencies, clearly labeled. If its first tier rate is 0.02 and second
0.005, their AEPs are 0.01980133 and 0.00498752. Expected payout is approximately
**297.76** and the loaded premium **479.20** in the chosen currency units. This is
distinct from the saved real San Antonio USGS curve.

Implement subsequent provider workers against the mapping tables above. The next
data requirements are a calibrated paired storm catalog, connected river/coastal
hydraulic transfers where local depth grids are absent, and compatible USFS BP/
conditional intensity layers with approved incident observations.
