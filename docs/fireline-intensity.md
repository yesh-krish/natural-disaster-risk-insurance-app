# Native fireline-intensity hazard

The Texas-only fireline-intensity option has been removed from the map because it does not supply annual exceedance probabilities. This document preserves the research and legacy API methods. The map retains **Wildfire / Potential intensity (kW/m)** and **Wildfire / Flame length (FSim)**.

## Source and physical meaning

The source is the Texas A&M Forest Service's Texas Wildfire Risk Assessment (TxWRAP), developed using Pyrologix/Vibrant Planet's Wildfire Exposure Simulation Tool (WildEST).

- [Native characteristic fireline-intensity ImageServer](https://gis.texaswildfirerisk.com/server/rest/services/TxWRAP/characteristic_fire_intensity_scale_img/ImageServer)
- [Machine-readable service metadata](https://gis.texaswildfirerisk.com/server/rest/services/TxWRAP/characteristic_fire_intensity_scale_img/ImageServer?f=pjson)
- [Federal IFTDSS definition and kW/m units](https://iftdss.firenet.gov/firenetHelp/help/pageHelp/content/20-models/lfb/out/firelintensitylfb.htm)
- [NWCG fireline-intensity definition](https://www.nwcg.gov/publications/pms205/nwcg-glossary-of-wildland-fire-pms-205/fireline-intensity-heat-release-7)

**Fireline intensity** is heat released per second per unit length of fire front. The dataset supplies **kW/m** directly. Physically, Byram's intensity is `I = H × w × R`: heat yield in kJ/kg, flaming fuel consumption in kg/m², and spread rate in m/s yield kW/m. NDApp reads native modeled intensity; it does not compute this equation from measured fuel or transform our existing flame-length thresholds.

The agency metadata describes this specific raster as **weather-weighted average heading-only fireline intensity**. WildEST weights simulations over wind and fuel-moisture scenarios using weather-type probabilities, including temporal frequencies and high-spread conditions. This is modeled potential fire behavior, not a live sensor observation or independently validated property damage. It describes heading fire, rather than every direction of spread.

## Scientific boundary: no invented annual probability

The service provides a characteristic intensity statistic, not an intensity exceedance distribution. Its weather weights do not supply the annual chance that wildfire occurs at a location. The separate TxWRAP “95th percentile” layer is not used here, and its weather-tail statistic must not be interpreted as 5% annual wildfire probability.

Consequently:

- The map shows an intensity value, source links, methodology, and CSV export.
- It hides the probability curve, trigger selection, payout inputs, and annual contribution for this hazard.
- The API returns `hazard_kind: "scenario_intensity"`, `annual_probability_available: false`, `hazard_value`, and `points: []`.
- Trigger-comparison requests return `status: "unavailable"`, `frequency_basis: "unavailable"`, no comparison rows, and an explanation. The modeled intensity remains available in the response where coverage exists.
- Automatic hazard ranking continues to compare hazards with usable probability curves. It does not compare a kW/m statistic against annual probabilities.

A defensible annual fireline-intensity curve would require compatible event-level intensity distributions and occurrence rates, or an authoritative annual exceedance product. Multiplying annual burn probability by this mean intensity would not produce such a curve. Heat flux at a building, exposure duration, embers, and building vulnerability remain separate questions.

## Sampling, coverage, and provenance

`fireline_intensity.py` queries `getSamples` using WGS84 longitude/latitude, `RSP_NearestNeighbor`, and the `None` raster function. It samples raw F32 values without rescaling rendered map colors. Before sampling, it verifies that metadata still advertises one F32 band, kW/m, weighted-average intensity, and heading-only behavior.

The observed service grid spacing is 10 by 10 in Web Mercator coordinates (EPSG:3857). This is the published service grid, not a claim of 10 m ground accuracy or source-model resolution. The service metadata does not publish an immutable model version or a model observation date. NDApp records the metadata URL/hash and sample request URL/hash plus sample retrieval time; retrieval time must not be mistaken for model vintage.

This is a Texas assessment. A location with no numeric source value is unavailable, including outside coverage; it never becomes zero. A native zero is retained as a modeled zero and does not establish no ember, neighboring-building, or property fire exposure. Invalid negative/nonfinite values and changed source units are rejected. Network failures remain explicit provider failures. Metadata is cached by UTC day and exact coordinate samples by a ten-minute UTC bucket; no spatial rounding is used.

Each response includes the native value/unit, statistic identifier `weather_weighted_mean_heading_fireline_intensity`, attribution, methodology links, sample retrieval timestamp, metadata/sample SHA256 hashes, sample URL, and grid metadata. CSV exports retain values, source URL, timestamps, hashes, and explicit `annual_probability_available=false`.

## Rectangular selections

The existing 5×5 sampling grid also works for this hazard. Here it averages **native kW/m values**, not probabilities. Zeros count; missing samples are excluded. The response reports all 25 sample locations, values and provenance, valid/missing counts, sum, minimum, maximum, and the spatial mean. A partially covered mean describes only the available samples. It is not an exhaustive raster-cell mean or the largest possible fire intensity in the rectangle.

The map displays the intensity mean, per-sample table/dots, and a CSV containing an average row and all 25 samples. Probability interpolation and the probability CSV do not apply. RAWS station metadata remains available as weather context; RAWS does not supply this modeled intensity.

## API examples

```text
GET /api/disaster?peril=fireline_intensity&lat=29.62&lon=-98.6
GET /api/v1/hazards/fireline_intensity?lat=29.62&lon=-98.6
GET /api/v1/areas/fireline_intensity?south=29.619&west=-98.601&north=29.621&east=-98.599
```

The v1 point endpoint also accepts a full US street address. Confirm the Census match and approximate coordinate before interpreting results. See `/api/docs` for the OpenAPI 1.2.0 contract.

## Verification

Offline checks cover raw sampling, units, metadata changes, provenance, zeros/NoData, invalid values, HTTP/API schemas, exclusion from contribution calculations, spatial averaging, station mapping, and UI switching/CSV behavior.

With the server running, reproduce live data-flow checks:

```powershell
.\.venv\Scripts\python.exe verify_fireline_api.py
```

The script saves `validation/fireline-intensity-live.json`. These checks verify source integration and software behavior; they do not validate modeled intensity against observed fires or validate insurance pricing.
