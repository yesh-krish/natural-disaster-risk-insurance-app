# Historical-weather potential surface-fire intensity

Implemented November 3, 2026, following the user's decision to implement [the historical-weather route](fsim-fireline-exceedance.md). This implementation request supersedes the earlier research-only restriction. Hazus is not used; [the Hazus finding](hazus-fire-research.md) still applies. [NASA observations and FRP](nasa-data.md) remain separate.

## Run and inspect

Install the normal requirements, restart the server, choose **Wildfire / Potential intensity (kW/m)** and select a **Point**. A verified vegetated example is **30.08, -97.30**, near Bastrop, Texas. A new point downloads historical inputs and may take several minutes; the page shows completed-year progress. Raw responses persist in ignored `data/potential_fire/requests/`; reusable year calculations and completed curves have separate integrity-checked caches. The existing flame-length view remains separate. The Texas-only native-intensity option has been removed from the map.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe server.py
```

Reproduce the calculation and export JSON plus annual-maxima CSV:

```powershell
.venv\Scripts\python.exe potential_fire.py --lat 30.08 --lon -97.3 --output data/potential_fire/bastrop-baseline.json
```

API: `/api/v1/hazards/potential_fire_intensity?lat=30.08&lon=-97.3`. The existing address parameter also works. Threshold illustrations use `/api/v1/trigger-comparisons/potential_fire_intensity?lat=30.08&lon=-97.3&payout_usd=10000`, with `frequency_basis=historical_potential_condition_frequency`. These are not wildfire loss prices. Areas return unavailable before downloading 25 baselines. Automatic hazard ranking does not select this research metric.

## Loading performance

The map uses `/api/potential-fire?lat=...&lon=...`: cached results return HTTP 200 immediately; new requests return HTTP 202 with a preparation phase and completed-year count. The browser polls once per second and stops when the selection changes. The versioned scientific API and CLI still return a complete result rather than a partial curve.

Up to four worker processes evaluate independent calendar years, preserving chronological moisture evolution within each year. At most two worker weather downloads run simultaneously. Two locations can prepare concurrently, with bounded outstanding years per location; a global all-location calculation lock is no longer used. Duplicate requests for one point share a calculation. Per-year results survive retries or server restarts and are keyed to the method/engine, numerical landscape parameters and both source-response hashes. Completed curves are keyed to the location and explicit scientific method identity, so unrelated code edits do not discard them. Bump `METHOD_VERSION` when changing equations, scenario, input vintages or calendar assumptions.

In the prepared 30-year pilot, cached-weather evaluation fell from **35.98 s sequentially to 9.08 s with four workers**. All annual maxima and curve points were identical. A completed disk-cache lookup took **0.023 s** in that run. This does not benchmark first-time network downloads; uncached locations still depend on provider response times. See [benchmark evidence](../validation/potential-fire/loading-benchmark.json) and [reproduction script](../validation/potential-fire/benchmark_loading.py). No hourly sampling or scientific assumptions were removed for speed.

A separate [fresh-location HTTP check](../validation/potential-fire/http-loading-check.json) completed in **59.14 s including downloads**, while returning its first progress response in **0.074 s**. The prepared pilot's live HTTP result took **0.213 s**. Network and machine conditions will change these timings.

The prepared app link is `/?peril=potential_fire_intensity&lat=30.08&lon=-97.3`; the hazard selector also offers an **Open the prepared Bastrop example** link. Shared links validate finite coordinates and supported hazard names before starting a lookup.

## Physical quantity and engine

PYRETECHNICS developed by Spatial Informatics Group, LLC and contributors. The pinned release has a [Community / Commercial dual licence](licenses/pyretechnics-2026.10.20.txt). This academic research prototype uses the Community terms; commercial use requires separate rights. The upstream licence is retained in both the local model installation and documentation. Open-Meteo service access has its own noncommercial restriction.

The quantity is **potential heading surface-fire intensity**, in **kW/m**, under a fixed landscape and live-moisture scenario. Byram intensity is `I = H w R`, with heat yield `H` in kJ/kg, flaming fuel consumption `w` in kg/m² and normal spread `R` in m/s. The surface engine uses the equivalent formulation: reaction intensity times residence time gives effective areal heat release `Q = H w` in kJ/m²; then `I = Q R`. Consumption and spread are modeled, not independently observed. There is no flame-length inversion or assumption that all mapped fuel burns.

[Pyretechnics](https://pyregence.github.io/pyretechnics/) **2026.10.20** supplies Rothermel surface behavior and intensity. Its [published source archive](https://pypi.org/project/pyretechnics/2026.10.20/) is pinned to SHA256 `1d79429c87325f45d0c56b2c0a82ae1930ffb3ff93db3cb52a340e1d59fb9c1f`. Required upstream modules and licence are retained unmodified under `data/potential_fire/model/`, with file integrity checks. Upstream's pure-Python mode plus Cython runtime avoids a Windows compiler installation and older compiled dependency pins. This is a limited source-mode integration, not installation of the full compiled package.

For each synchronized hour, fuel moisture drives surface spread and heat release. The engine uses fuel-bed load, depth, surface-area ratios, heat content, extinction moisture and dynamic herbaceous load transfer. Its 10 m → 20 ft → midflame wind adjustment accounts for canopy sheltering and fuel depth. Wind direction and slope/aspect are combined, with the engine's effective-wind limit enabled. Crown-fire transition and intensity are excluded.

## Data, units, resolution and access

| Input | Source and units | Support and limitation |
| --- | --- | --- |
| Historical weather | [Open-Meteo historical API](https://open-meteo.com/en/docs/historical-weather-api), explicitly `models=era5`: temperature °C, RH %, rain mm per preceding hour, 10 m wind m/s and meteorological direction degrees. | Global ERA5 **0.25°**, hourly reanalysis, **1991–2020** baseline. Elevation downscaling disabled. Free endpoint needs no key for noncommercial use; commercial use requires appropriate service access. |
| Snow | Same API, explicitly `models=era5_land`, snow depth m. | **0.1°** ERA5-Land, separately downloaded and exactly aligned by UTC hour. ERA5 does not expose snow depth through this API. Positive snow depth suppresses surface spread in this scenario; coarse snow screening can over-suppress patchy snow. |
| Fuel model | [USGS LANDFIRE LF2023 FBFM40](https://edcintl.cr.usgs.gov/arcgis/rest/services/Landfire_LF2023), integer Scott–Burgan model code. | Nominal **30 m** nearest native samples, region-specific CONUS/AK/HI services. All three have routing; unsupported/nonburnable/NoData cells remain unavailable, not zero ember or building-fire risk. |
| Wind sheltering | Same LF2023 folder: canopy cover %, canopy height stored in tenths of metres. | Converted to fraction/metres, then feet where required by the engine. Missing values do not become open canopy. |
| Terrain | [LANDFIRE 2020 topography](https://edcintl.cr.usgs.gov/arcgis/rest/services/Landfire_Topo): slope degrees, downslope aspect degrees. | Nominal 30 m; slope converted to rise/run. Flat-cell undefined aspect is allowed. No local wind-flow model. |
| Live moisture | Explicit herbaceous **60%**, woody **90%**, water mass/dry fuel mass. | Fixed scenario values, not historical measurements or a phenology model. They affect curing and spread and need sensitivity/expert review. |

A 30 m sample does not create 30 m weather accuracy. USGS services are public without an account; raw responses, URLs, retrieval times and hashes are retained. Only the Bastrop point has a complete live-data baseline here; AK/HI routing is tested, not nationwide scientific accuracy.

## Moisture and temporal processing

The simplified moisture chain follows [Mandel et al. (2014), WRF-SFIRE](https://doi.org/10.5194/nhess-14-2829-2014), its [model explanation](https://wiki.openwfm.org/wiki/Fuel_moisture_model) and [official WRF implementation](https://github.com/wrf-model/WRF/blob/master/phys/module_fr_fire_phys.F). This is standalone use of those equations, not a coupled WRF simulation or NFDRS4/Nelson moisture implementation.

Temperature/RH give separate drying/wetting equilibria. Dead 1/10/100 h moisture follows the exact exponential time-lag step; between the two equilibria it stays unchanged. Rain uses a 0.05 mm/h threshold, 2.5 kg/kg saturation, 8 mm/h intensity scale and 1.4/14/140 h soaking parameters. These are upstream defaults without local calibration, moisture assimilation or explicit solar heating. Each year has **31 preceding spin-up days**, discarded from annual maxima.

Every baseline and spin-up hour must have finite inputs in range. Units and exact timestamps are checked. Missing inputs exclude the entire year; duplicate/out-of-order hours or changed units fail the calculation. There is no imputation. Years have **UTC** boundaries, and selected coordinates are normalized to six decimal places. Modeled zero-spread years remain valid; missing data never become zero.

## Exceedance interpretation

For each complete year, `M_y = max(hourly potential intensity in year y)`.

`frequency_pct(i) = 100 × count(complete years with M_y >= i) / count(complete years)`

All complete years enter the denominator, including zeros. At least **20 complete years** are required by product policy. Thresholds are distinct annual maxima, preserving ties and inclusive `>=` exceedance. The app uses exact supported levels, without fitted tails, trigger interpolation or extrapolation. A 30-year sample's smallest nonzero empirical frequency is **1/30 = 3.33%**; it cannot independently establish a 1% or 0.2% annual level. Dependence, climate nonstationarity and model error limit interpretation; this is not a calibrated future probability.

The response states `hazard_kind=potential_intensity_exceedance` and `actual_wildfire_probability_available=false`. `annual_probability_available=true` enables the **potential-condition** curve only. A displayed payout illustration assumes that modeled conditions trigger payment even without a fire. Do not multiply this curve by an unrelated burn-probability raster to claim actual-fire AEP.

## Verification and professor-ready finding

The live pilot at **30.08, -97.30** uses fuel model **188**, canopy cover **75%**, height **23 m**, slope **9°**, aspect **310°**. All **30 years** passed complete-hour checks. For example, **107.0535 kW/m** was met in **2/30 years**, giving **6.67% historical annual potential-condition exceedance**. These are calculated prototype outputs, not observed historical fire intensities. Source hashes and peak-hour states are retained locally.

Tests cover analytical moisture response, hysteresis, rainfall, spin-up exclusion, leap years, missing inputs, units, duplicate timestamps, snow suppression, zero years, ties and the annual denominator. API tests preserve interpretation and validate the real pilot against OpenAPI. UI tests exercise selection, annual tables, thresholds, payout meaning, export and stale-data clearing. Checking `I = Q R` verifies units/integration, not independent scientific accuracy.

**Finding:** historical-weather Byram/Rothermel potential intensity and annual exceedance are implemented with real inputs and auditable annual maxima. This supplies a selectable physical alternative to flame length. It does not yet establish improved accuracy or actual wildfire/damage probabilities.

**Open questions:** sensitivity to live moisture and simplified dead-moisture modeling; validation against independent historical spread/intensity observations; effects of crown fire, unresolved winds and patchy snow; and whether the professor wants a fixed modern landscape or historical fuels. The next scientific step is sensitivity analysis and independent-fire validation at representative sites before nationwide accuracy claims.
