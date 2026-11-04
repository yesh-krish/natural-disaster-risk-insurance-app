# FEMA Hazus: relevance to NDApp

Research date: October 29, 2026. This assessment compares public FEMA/USACE documentation with the current NDApp source. Recommendations below are engineering judgments, not completed integrations or calibrated insurance results.

## Recommendation

Use Hazus first to strengthen NDApp's **flood vulnerability and loss layer**, then to investigate how well a proposed parametric payout tracks modeled damage. Run a small building-level pilot before adopting the full desktop application. Hazus is also useful as a source of reference studies and earthquake methodology.

Its main contribution to us is the link from hazard intensity to consequences:

**Hazard depth or shaking + exposed buildings + vulnerability functions → modeled damage and loss.**

FEMA describes Hazus as a standardized methodology with baseline inventories and models for flood, earthquake, hurricane, and tsunami impacts. Input quality affects results, and FEMA cautions that modeled results contain uncertainty. [FEMA introduction](https://www.fema.gov/sites/default/files/documents/fema_what-is-hazus-factsheet.pdf).

## What we already use

- `damage_model.js` contains four **Hazus 4.0 riverine RES1 structure-damage curves**, IDs 129, 704, 107, and 108. They cover one/two stories with/without a basement.
- The app subtracts first-floor height from ground-level flood depth before interpolating damage. It reports a structure-damage percentage and does not extrapolate outside the curve's range.
- TWDB supplies our four flood-depth scenarios at 20%, 10%, 1%, and 0.2% annual exceedance probability. The depth products combine flood perils, whereas the selected damage functions are riverine. That mismatch remains an exploratory assumption.
- Our selected fixed payout is independent of this damage estimate. The contribution calculation uses trigger probability multiplied by payout.
- Rectangle analysis averages local hazard probabilities across 25 sample locations. It does not enumerate buildings or calculate a portfolio loss distribution.

Evidence: `README.md`, `damage_model.js`, `disaster_data.py`, and `docs/system-architecture.md`, especially its exploratory flood damage and operating limits sections.

## Ranked opportunities

| Priority | Resource or capability | Benefit for NDApp | Requirement or limit |
| --- | --- | --- | --- |
| 1 | Flood Assessment Structure Tool (FAST) and flood methodology | Broaden the current four-profile demonstration into building-level damage and dollar-loss research | Building inventory, replacement values, compatible depth rasters, verified function assignments |
| 1 | Damage versus parametric payout comparison | Expose scenarios where a trigger pays too little, pays without comparable damage, or fails to pay despite damage | Hazard and damage must describe the same event and location; scenario examples alone do not quantify annual basis risk |
| 2 | Hazus Loss Library | Compare our results with documented reference studies; inspect modeling inputs and outputs | Match event, geography, inventory, model version, and loss definition; library outputs are modeled benchmarks |
| 2 | Hazus inventory / USACE National Structure Inventory | Supply initial building exposure data for area studies | Treat estimated attributes as assumptions; confirm individual property characteristics |
| 3 | Earthquake methodology and ShakeMap workflow | Extend our current PGA hazard view toward event damage research | Additional building and shaking inputs; a PGA curve alone is insufficient for the general building method |
| 3 | Hurricane loss methodology and national studies | Add loss context and investigate a future wind vulnerability layer | Reconcile sustained wind with peak gust; verify current software capabilities and available data |

### 1. FAST is the closest technical fit

FEMA's FAST factsheet describes an open-source tool that combines building inventories, flood depths, and assigned damage functions. Inputs include location, occupancy, floor area, first-floor height, foundation, stories, and replacement cost. This aligns with our existing Python/raster workflow and would support more building types and loss measures. [FEMA FAST factsheet](https://www.fema.gov/sites/default/files/documents/fema_flood-assessment-structure-tool.pdf).

The `nhrap-hazus/FAST` repository used for our historical coefficients explicitly says it no longer receives updates and directs users to FEMA's Map Service Center for the latest version. Therefore, retain our existing coefficient provenance and compare it with the current official package before changing curves. A shared function ID does not establish identical coefficients. [Legacy FAST repository](https://github.com/nhrap-hazus/FAST).

**Proposed implementation:** begin with a separately versioned flood-loss adapter that records building inputs, vulnerability source/function ID, depth source, units, damage fraction, and replacement-value year. Keep model versions reproducible. Review the actual package's dependencies and reuse terms before incorporating its code.

### 2. The key research question is payout adequacy

For a matched scenario, compute:

`modeled structure loss = damage fraction × building replacement value`

Then compare that modeled loss with the contract's payout for that scenario. For example, using our existing one-story/no-basement curve, 3 ft of ground-level water and a 1 ft first-floor height imply 2 ft of water relative to the floor and **32% structure damage**. An illustrative $250,000 replacement value gives **$80,000 modeled loss**; a $50,000 payout, if triggered, leaves a **$30,000 modeled shortfall**. These are hypothetical inputs, not a property quote or observed loss.

This comparison would help investigate **basis risk**, the mismatch between payout and actual loss. Hazus supplies modeled damage, so it cannot by itself establish actual basis risk. Quantifying that requires suitable observations or a justified joint event/loss model. Neither average annual loss nor a damage percentage should replace the probability input in our fixed-payout calculation.

### 3. Annual flood loss is possible research, with explicit limits

FEMA's Hazus 7 guidance allows flood average annualized loss analyses using at least three return-period depth grids. Our four TWDB return periods are a potential starting set, subject to compatible coverage and input validation. [Hazus 7 user guidance](https://www.fema.gov/sites/default/files/documents/fema_hazus_7_user_guide.pdf), [Hazus 7 release notes](https://www.fema.gov/sites/default/files/documents/fema_hazus_7_release_notes.pdf).

Do not sum the four scenario losses or four cumulative exceedance probabilities. A research estimate needs a documented loss-frequency integration method, interpolation, and treatment of the frequent-event and rare-event tails. Missing TWDB cells must remain missing. Do not copy fixed weights intended for a different return-period set. FEMA's flood manual discusses the annualization methodology and requires user-provided depth grids for the analyzed return periods. [Flood technical manual](https://www.fema.gov/sites/default/files/documents/fema_rsl_hazus-7-fltm_06272025_0.pdf).

### 4. Reference studies and building inventories

The **Hazus Loss Library** provides historical, deterministic, and probabilistic studies, with available downloads including reports, CSV, GeoJSON, and shapefiles. It could provide comparison cases without our running the desktop model. We must inspect study metadata before comparing results; agreement between two models is not observed-loss validation. No San Antonio study coverage was confirmed in this investigation. [FEMA library factsheet](https://www.fema.gov/sites/default/files/documents/fema_hazus-loss-library-factsheet.pdf), [library guide](https://www.fema.gov/sites/default/files/documents/fema_hll_how_to_guide_final.pdf).

The related **USACE National Structure Inventory (NSI)** offers public building points through an API and downloadable GeoJSON/GeoPackage data. Its 2022 documentation describes estimated attributes and assumptions, so use it as an initial research inventory with provenance, not verified parcel facts. Do not assume NSI and a particular Hazus release use identical inventories. [USACE NSI documentation](https://www.hec.usace.army.mil/confluence/nsi/technicalreferences/2022/technical-documentation).

Building inventory would let us sum scenario losses over real exposure locations. Our existing equally weighted grid probability average cannot substitute for that exposure calculation. Shared event footprints would still be necessary to study correlated payouts, reserves, and tail losses.

### 5. Earthquake and hurricane are later extensions

The latest Hazus release verified here is **7.2**, announced June 4, 2026, with USGS ShakeMap integration in ArcGIS Pro. This makes event-impact research relevant to our USGS work. [FEMA June 2026 bulletin](https://content.govdelivery.com/accounts/USDHSFEMA/bulletins/41a9b67).

However, Hazus general building earthquake damage uses building type, design level, and response-spectrum/capacity/fragility methods. Do not attach a generic damage percentage directly to NDApp's existing PGA-only curve. [FEMA earthquake technical manual, §5.1](https://www.fema.gov/sites/default/files/documents/fema_hazus-earthquake-model-technical-manual-6-1.pdf).

Our cyclone data are historical sustained-wind footprint frequencies. Hazus wind documentation distinguishes peak 3-second gust from 1-minute sustained wind; those metrics require compatible treatment before applying damage functions. [FEMA hurricane guidance](https://www.fema.gov/sites/default/files/documents/fema_hazus-6.1-hurricane-model-user-guidance.pdf).

FEMA also announced **P-2429**, a national hurricane wind annual-loss assessment, in September 2026. It is worth inspecting for regional comparison and mitigation context. The announcement does not establish an address-level wind exceedance API or resolve our historical-frequency calibration gap. [FEMA September 2026 bulletin](https://content.govdelivery.com/bulletins/gd/USDHSFEMA-42bfddc?wgt_ref=USDHSFEMA_WIDGET_EA).

## What Hazus does not resolve for us

- **Nationwide flood depth coverage:** FAST needs depth inputs, and the cited annual-loss workflow requires return-period depth grids. Adopting it does not supply our missing nationwide point curves.
- **Standalone wildfire or rainfall coverage:** these are not among the four Hazus models described by FEMA. Retain our separate USFS and NOAA sources.
- **Contract settlement:** modeled damage is a research comparison; our specified trigger and accepted measurement source still determine payment.
- **Pool solvency:** average annual loss does not establish correlated portfolio tail risk or required reserves.
- **Desktop integration:** Hazus in ArcGIS Pro introduces a separate desktop dependency and licensing consideration. Select it only if a defined study needs that workflow.

## Concrete next step

Run a **small San Antonio flood-loss pilot** over confirmed TWDB coverage:

1. Obtain the current official FAST package and verify its function tables against our four archived curves.
2. Assemble a small building inventory with confirmed or explicitly assumed foundation, first-floor height, stories, occupancy, and replacement value.
3. Calculate structure loss for each available TWDB scenario, documenting the riverine/combined-peril assumption or selecting compatible functions where justified.
4. Compare matched scenario losses with one chosen trigger/payout schedule, including non-triggering damage scenarios where supported.
5. Compare equivalent results with FAST or an appropriately matched Hazus study, then seek independent observed-event evidence for calibration.

Deliverable: a reproducible CSV and a damage-versus-payout comparison with source/version metadata. Implementing this pilot is a recommendation; no application behavior was changed by this investigation.

## Access and verification limits

Some FEMA pages returned 403/502 responses and the interactive Loss Library could not be inspected directly. Findings use accessible official FEMA bulletins, indexed official manuals/factsheets, the FEMA-linked legacy FAST repository, and USACE documentation. The current FAST download/version, current desktop feature parity, and local library coverage remain to be checked from the actual official packages/services. No software was installed and no Hazus/FAST analysis was run.
