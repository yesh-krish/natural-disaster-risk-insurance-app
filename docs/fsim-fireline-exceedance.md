# Fireline intensity with annual exceedance probabilities

Research checked November 3, 2026. Scope: a United States fireline-intensity hazard curve for NDApp, using published datasets and explicit annual frequency. This research initially left application code unchanged. The user subsequently authorized [the separate historical-weather potential-intensity implementation](potential-fire-intensity.md); it estimates annual potential-condition exceedance, not FSim actual-fire arrival AEP.

**Metric decision:** select **fireline intensity (kW/m)** as the new wildfire measure for further development. Among the non-flame-length measures reviewed, it offers the clearest established wildfire-model pathway to annual site exceedance. This selects a physical metric, not an available national curve: native event-intensity data and valid annual weights remain an integration requirement. If immediate use of published annual-exceedance inputs is the overriding constraint, flame-length classes remain the available option verified in this research; heat flux, accumulated exposure, total heat release and spread rate did not yield a verified ready-to-use national annual curve.

**Finding:** an annual fireline-intensity curve is scientifically feasible, but no ready-to-use public nationwide kW/m exceedance dataset was verified. The public FSim packages inspected contain burn probability and flame-length classes. Native intensity events or histograms must be obtained from the model producers or generated in a calibrated simulation. This is a materially larger task than reading an existing USGS hazard curve.

## The quantity we need

Use **Byram fireline intensity**, `I = H × w × R`, in **kW/m**: combustion heat yield (kJ/kg) × fuel consumed in the flaming front (kg/m²) × perpendicular spread rate (m/s). This expresses heat release per unit fire-front length; it is not a sensor reading, building heat flux, radiative power, burn severity, or loss. A new metric is not evidence of improved accuracy. [USFS scientific framework, pp. 24–28][S1]

For each site `x`, the desired curve is:

`p_x(i) = P(at least one wildfire reaches x with intensity ≥ i during a year)`

This requires the probability of fire arriving **and** the distribution of intensity when it does. A potential-intensity map supplies neither the complete occurrence model nor an annual curve by itself.

**Why the existing layer is Texas-only:** `fireline_intensity.py` uses a Texas A&M Forest Service TxWRAP assessment. Its geographic limit belongs to the source, not to the intensity metric. That raster is a weather-weighted mean heading intensity, with no annual occurrence distribution. [TxWRAP metadata][T1]

FSim is the Forest Service's **Large Fire Simulation System**. It models synthetic fire seasons with weather, ignition, growth and suppression modules. Thousands of modeled seasons permit estimates beyond the sparse history of actual burns at one location. Its original scientific study explains the simulation and calibration; a simulated season is not another observed historical year. [Finney et al. (2011)][P1]

## What actual datasets provide

| Dataset / method | Verified contents, support and access | Can it provide the requested curve? |
| --- | --- | --- |
| **FSim third edition, Dillon et al. (2023), RDS-2016-0034-3** [metadata][D1], [file index][D2] | CONUS, Alaska and Hawaii, **270 m**. Regional `BP.tif` and `FLP1`–`FLP6.tif`; FLP bins are <2, 2–<4, 4–<6, 6–<8, 8–<12, ≥12 ft. Public, no additional fees/permission required under source terms. The app already ingests BP/FLP5/FLP6. | Supplies burn likelihood and conditional **flame-length** classes. No native kW/m distribution listed. Cannot silently relabel these as a new intensity measure. |
| **FSim climate runs, Riley et al. (2025), RDS-2025-0006** [metadata][D3], [file index][D4] | **CONUS only**, 270 m. LF2020 landscape; baseline climate 2004–2018 and projected 2040–2054. Download updated July 1, 2025. Public archive contains BP plus six FLP rasters for each climate and geographic summaries. | Promising access lead: metadata says complete per-fire flame-length and arrival-time data are available. **Those records are not listed in the public ZIP directory inspected**, and native fireline intensity/fire type were not verified. Future climate is not a present-day replacement. |
| **Wildfire Risk to Communities, 2024 second edition** [catalog][D5], [product list][D6] | Public regional downloads, 30 m published grids. BP is refined from 270 m FSim; behavior uses WildEST and updated fuels. Published measures include BP, conditional flame length and conditional 4-/8-ft exceedance. | Useful alternative flame-length analysis, not a verified native kW/m annual curve. Finer published pixels do not establish finer independent burn-probability information. |
| **Native FSim intensity outputs** [developer guide, §4][S2] | `_MeanIntensity.asc` is conditional **mean** kW/m, excluding unburned iterations. Standard output also includes BP and flame-length probabilities. Native per-event intensity export is not established by the guide. | A mean plus BP cannot reconstruct a distribution. Need additional output retained by producers or a modified/repeated model workflow. Software/source access for such an export remains unverified. |
| **ELMFIRE as an alternative simulation engine** [output specification][E1], [license/access][E2] | Produces per-case `flin_` fireline-intensity rasters in **kW/m** (`DUMP_FLIN`), arrival time and burned-state outputs; accepts fuels/topography/weather. Runs on Linux/WSL. Current documentation identifies AGPLv3 plus Commons Clause and separate commercial licensing. | Technically supports generating needed native intensity records. Its ordinary fraction of ensemble cases burned is **not automatically annual**. Needs an occurrence model, weather weighting, calibration and retained case/year identity. New model outputs would be derived research data, not an existing national dataset. |

No claim of exhaustive absence: this verifies the identified public packages, not every agency's internal event archive. Regional assessments and vendor products may have additional outputs; their names alone do not establish annual kW/m distributions or usable access terms.

### Package inspection evidence

Saved original agency metadata and file indexes in [`samples/fsim-exceedance/`](samples/fsim-exceedance/), with URLs, retrieval times and SHA256 in [`source-manifest.json`](samples/fsim-exceedance/source-manifest.json).

The 2025 catalog points to a USFS Box-hosted ZIP. Its actual directory was read with **19,075 bytes of HTTP range traffic**, without downloading the **1,709,275,509-byte** archive. The directory has **150 entries**, including **14 GeoTIFFs**: BP and FLP1–6 for each climate. No event, arrival-time or native intensity raster is present in the directory. See [`fsim-2025-archive-inventory.json`](samples/fsim-exceedance/fsim-2025-archive-inventory.json). This verifies package contents, not the full archive checksum or raster values. A guessed legacy archive path returned 404; the catalog's actual Box link worked.

The gap between the 2025 metadata's event-data statement and the downloadable inventory is an **access/schema question**, not proof the event data do not exist. The record names Karin Riley as the data contact. No message was sent.

## A defensible calculation

**Preferred route: native intensity records grouped into complete simulation years.** Obtain or generate records with `run_id`, `origin_pyrome`, `simulation_year`, `event_id`, site/grid identity, burned state, `fireline_intensity_kw_m`, model version, landscape vintage and occurrence weights. Retain an independent year manifest, including years with no events. Define whether intensity means flaming-front passage or a maximum over time and verify the model's raster semantics before aggregation.

For each complete equally weighted simulated year `y`, compute:

`M_y(x) = max intensity at x over all fires reaching x in year y`

For positive thresholds, non-burning years are non-exceedances. Then:

`p_hat_x(i) = number of years with M_y(x) ≥ i / total simulated years`

Count a year once, even if multiple fires exceed the threshold. Do not divide by burning years, by event count, or by the number of weather scenarios. Missing output is unknown, not an unburned year. This estimator is our proposed aggregation of annual event records; it is not a claim that the public FSim ZIP already supplies those records.

If producers supply a **conditional distribution of annual-maximum intensity given an annual burn**, use `p(i) = BP × P(M ≥ i | annual burn)`, with matched model, vintage, geography and conditioning. An event-conditioned intensity distribution can differ where repeat burns occur.

If only event rates `w_e` in events/year are available, `lambda(i) = sum(w_e × 1[I_e(x) ≥ i])` is an **exceedance rate**. The conversion `p(i) = 1 − exp(−lambda(i))` additionally assumes Poisson event occurrence. Without that justification, report the rate, not an exact annual probability. Independent pyrome runs may have different simulation lengths and incompatible year IDs; concatenating their counts and dividing by one denominator is invalid.

Export supported thresholds, probabilities, valid-year counts, exceedance counts, uncertainty method, spatial resolution and provenance. Curves must be non-increasing. At rare thresholds, few simulated exceedances imply unstable estimates; zero exceedances do not establish zero real risk. Avoid unsupported tail extrapolation and return periods beyond the evidence.

## Actual inputs for a new native-intensity simulation

This is a feasible fallback if producer records cannot be obtained; it is **not an integration completed here**.

| Input | Published source / variables | Limitation and preparation |
| --- | --- | --- |
| Fuels, canopy and terrain | [LANDFIRE FBFM40][I1] and corresponding canopy/topography products: categorical fuel model, canopy cover, height/base height, bulk density, elevation, slope and aspect. Downloads/services cover CONUS, AK and HI; availability/version varies by region. | Pin one coherent release and decode each band's units/scaling. Match baseline LF2020 for replication; updated fuels define a different study. A cell's fuel category is not measured flaming consumption. |
| Ignition occurrence and calibration | [FPA-FOD sixth edition][I2]: 1992–2020 U.S. records, discovery date, location, final size and cause. Public download; roughly 2.3 million records. | Locations need only be precise to a one-square-mile section; reporting is incomplete. Fit seasonal/spatial ignition occurrence and large-fire escape/size behavior; do not use ignition count as site burn probability. |
| Weather and fuel-moisture drivers | [gridMET][I3]: daily ~4 km CONUS data from 1979; temperature, humidity, precipitation, wind and derived ERC/dead-fuel moisture. [RAWS historical archives][I4] support station weather/wind context. | Daily gridded data are not ready-made hourly wind/fuel-moisture streams. Model/downscale appropriately; preserve weather dependence and station reference heights. gridMET does not supply AK/HI coverage, so a full-U.S. study needs matched regional weather sources. |

Fit and check the occurrence model against withheld fire history; reproduce burn-probability and fire-size patterns before using intensity curves. Burned-area calibration does not independently validate kW/m intensity. Model native intensity from fuel consumption and spread, retaining surface/crown treatment and relative spread direction. Estimate intensity uncertainty separately from event frequency.

**Effort:** reading existing probability rasters is low effort; obtaining and processing existing producer intensity events is medium-to-high depending on schema and volume; creating and validating a national annual simulation is high effort and requires fire-model expertise and substantial compute. No commercial software terms or data procurement were accepted.

## Shortcuts that fail the stated objective

- `annual BP × mean intensity` has intensity units and is not a probability.
- A TxWRAP weather-tail statistic is not an annual wildfire exceedance probability.
- A daily potential-intensity annual maximum estimates **potential behavior under weather**, not the chance an actual fire reaches the site.
- FLP classes cannot generally become exact intensity thresholds through one inverse formula: surface and crown fires use different intensity–flame-length relations, classes discard within-bin detail, and the top class is unbounded. [USFS framework][S1]
- Perimeter intersections alone supply no site intensity. A historical USFS assessment explicitly notes that its per-fire pixel intensity was not recorded; do not assume all FSim event sets retain it. [Island Park assessment, p. 12][S3]

## Byram intensity using historical data: verified examples

Historical observations can support `I = H w R`; the result still depends on how flaming fuel consumption and spread are measured or modeled. Historical weather alone is insufficient.

| Example | Historical inputs and output | Relevance and remaining limitation |
| --- | --- | --- |
| [PT-FireSprd v2 (Benali et al., 2025)](https://zenodo.org/records/16279793) | Reconstructs **155 large Portuguese wildfires, 2015–2024**, from imagery and incident evidence. Explicit Byram head-fire intensity, **kW/m**, assumes complete consumption of mapped fuel load. Progressions and behavior maps; no uniform grid resolution established here. | A concrete historical-fire intensity dataset, not a U.S. dataset or complete annual site record. Selected fires cannot directly establish annual local exceedance. The 251.8 MB download carries an author note restricting use until the accompanying paper is published; fulfillment and detailed licensing were not verified. Medium effort to inspect/validate; national transfer requires new reconstruction. |
| [Salazar and Bradshaw (1984), USFS PSW-RP-174](https://research.fs.usda.gov/download/treesearch/28832.pdf) | Northern Rockies historical station weather, modeled moisture, fuel types and terrain produce Byram intensity probability distributions. Figure 4 gives cumulative distributions; **1 Btu/(s ft) = 3.4592 kW/m**. Station/test-case support, not a national raster. | A U.S. scientific precedent for history-to-modeled-intensity distributions. These are behavior/weather frequencies, not annual site-burn probabilities. Open report; no current operational dataset verified. Reproducing with modern inputs is substantial modeling work. |
| [CSIRO NBIC-ACS Stage 2 potential-intensity dataset (2026)](https://data.csiro.au/collection/csiro:65939?tab=data), [publisher metadata via Research Data Australia](https://researchdata.edu.au/nbic-acs-stage-exceedance-probabilities/3951629) | Australia: 90 m fuel classification, 43+ years of hourly weather, extreme-value analysis; potential head-fire intensity **kW/m** at 5%, 2%, 1% AEP. Fuel source data can be 500 m or coarser. Open, CC BY-NC 4.0. | Strong precedent for annual **potential** intensity curves. The [AFDRS methods paper](https://doi.org/10.1071/WF23142) explicitly calculates Byram intensity from spread, available fuel and heat yield. Exact Stage 2 implementation equivalence remains to be checked. Australian outputs cannot supply U.S. values; building a U.S. analogue is high effort. |

**Probability interpretation:** with one modeled potential-intensity series per site, retain chronological years and compute `A_y = max_t I_potential(y,t)`. The empirical annual exceedance is `count(A_y >= i) / number of complete years`. This is our proposed estimator for dangerous **potential conditions**; it does not imply that a fire occurred. Historical event-based annual exceedance instead needs intensity at the site for all relevant fires and a complete annual observation record, including genuinely unburned years. Neither a daily percentile nor the fraction of selected fire runs exceeding a threshold is that annual probability.

**Practical research next step for the historical route:** test a small CONUS pilot with a documented surface-fire model, LANDFIRE fuel parameters and historical weather, preserving the full model chain for moisture, wind adjustment, spread and flaming consumption. Report potential kW/m and potential-condition AEP explicitly, and validate reconstructed behavior against independent fires before extending geography. This is an alternative research track to obtaining FSim event records; it does not replace actual-fire AEP without an occurrence model. Open questions are consumption assumptions, surface/crown treatment, temporal weather resolution, validation and whole-U.S. weather coverage. No dataset above was ingested into the application.

## Recommendation and decision

**First obtain a small extract of the 2025 FSim baseline event data and verify native intensity availability**, before building another simulator. The concrete data request is: native per-cell/per-event kW/m (or native threshold counts), burned mask, surface/crown state, event/year/pyrome identifiers, simulation-year totals/weights, exact model/version and source-grid metadata. If only flame length/arrival time is available, request the native intensity output or sufficient matched model state to reproduce it; do not assume it is recoverable from those two fields alone.

Start with a few representative CONUS cells for processing feasibility, while keeping **CONUS + AK + HI** as the desired coverage. A small extract tests access and aggregation; it does not validate nationwide accuracy. If producers cannot supply native intensity events, a calibrated native-output engine such as ELMFIRE is the research fallback. The existing national flame-length curve remains available during that work.

The supported result today is a **specific dataset lead and implementable probability method**, with the missing native-intensity records clearly identified. No real kW/m exceedance values were invented, and no new app curve was installed. [`nasa-data.md`](nasa-data.md) already covers observations/FRP; that research is not duplicated here. Hazus suitability remains documented in [`hazus-fire-research.md`](hazus-fire-research.md).

[T1]: https://gis.texaswildfirerisk.com/server/rest/services/TxWRAP/characteristic_fire_intensity_scale_img/ImageServer
[P1]: https://research.fs.usda.gov/download/treesearch/39312.pdf "Finney et al. 2011, original FSim scientific study"
[S1]: https://www.fs.usda.gov/rm/pubs/rmrs_gtr315.pdf "Scott, Thompson and Calkin 2013, USFS scientific framework"
[S2]: https://pyrologix.com/wp-content/uploads/2019/11/FSimBestPractices_0.3.1.pdf "Scott, Short and Finney 2018, FSim developer guide, §4"
[S3]: https://research.fs.usda.gov/download/treesearch/52774.pdf "Helmbrecht et al. 2016, Island Park study, p. 12"
[D1]: https://www.fs.usda.gov/rds/archive/products/RDS-2016-0034-3/_metadata_RDS-2016-0034-3.html
[D2]: https://www.fs.usda.gov/rds/archive/products/RDS-2016-0034-3/_fileindex_RDS-2016-0034-3.html
[D3]: https://www.fs.usda.gov/rds/archive/products/RDS-2025-0006/_metadata_RDS-2025-0006.html
[D4]: https://www.fs.usda.gov/rds/archive/products/RDS-2025-0006/_fileindex_RDS-2025-0006.html
[D5]: https://www.fs.usda.gov/rds/archive/catalog/RDS-2020-0016-2
[D6]: https://wildfirerisk.org/download/
[E1]: https://elmfire.io/user_guide.html#outputs "ELMFIRE native outputs and Monte Carlo behavior"
[E2]: https://elmfire.io/ "ELMFIRE runtime and current license terms"
[I1]: https://www.landfire.gov/fuel/fbfm40
[I2]: https://www.fs.usda.gov/rds/archive/products/RDS-2013-0009.6/_metadata_RDS-2013-0009.6.html
[I3]: https://www.climatologylab.org/gridmet.html
[I4]: https://raws.dri.edu/
