# Monitor records: per-station exceedance tables

Added **November 22, 2026**. Under Rainfall, Wind and Flood in Point mode, the
viewer lists each nearby monitor's own historical annual maxima, nearest first.
Heat has its own equivalent table (see [heat-and-wind.md](heat-and-wind.md)).
Earthquake and wildfire have no such table: seismic stations rarely record strong
shaking within their short operating periods, and no monitor measures a fixed-point
fire hazard.

Rows are **never averaged or combined**, and they do **not** change the map's
curve, trigger or contribution. Those still come from NOAA Atlas 14 (rainfall),
HURDAT2 tropical-cyclone footprints (wind) and TWDB flood-depth rasters (flood).

## Sources and rules

| Table | Source | Annual value | Period |
| --- | --- | --- | --- |
| Rainfall | NOAA GHCN-Daily `PRCP` (tenths of mm) | Wettest calendar day | 1991–2020 |
| Wind | NOAA GHCN-Daily `WSF2` (fastest 2-minute wind, tenths of m/s) on tropical-storm days only | Strongest sustained wind during a storm (0 if no storm) | 2004–2025 |
| Flood | NOAA NWPS gauges with official flood stages + USGS annual peak gage heights (ft) | Highest river level per water year (Oct–Sep) | Each gauge's systematic record |

- **Station selection:** within 100 km, nearest first, up to the viewer's
  "Stations (n)", capped at **10** because each station is a separate download.
  GHCN stations must have an inventory span overlapping the period by at least 20
  years. Flood gauges must be NWS gauges with a minor flood stage and a linked USGS record.
- **Counted years (GHCN):** a calendar year counts when at least 95% of its days
  have a valid, unflagged value (347 of 365, 348 of 366). This is less strict than the
  historical-heat curve, which requires every day. Skipped days are never imputed.
  Because a missing day can only hide a larger value, maxima can be biased low.
- **Wind sanity guard:** wind values above 60 m/s are treated as invalid days and
  tallied per station (shown in the table). Early ASOS records repeat an implausible
  65.7 m/s value at many stations. Values under the guard are kept as reported.
- **Flood levels:** the yearly value is the higher of the USGS peak-flow gage height
  and the separate maximum gage height. Historic peaks (peak code 7) and readings at a
  different site or datum (gage-height codes 3 and 6) are left out.
- **Short records (changed November 22, 2026):** stations with fewer than 20
  counted years are listed with their year count and a frequency labelled
  "Short record: under 20 years — unreliable". With *n* years each year is worth
  100/*n* percentage points, and an event with a true 10% annual chance shows 0% in
  about 35% of 10-year records (12% of 20-year, 4% of 30-year). Stations with zero
  counted years show the reason instead. The API row keeps `status:
  "insufficient_years"` with its `annual_maxima`; the label is added in the browser.
- **Frequency:** `100 × counted years with maximum ≥ threshold / counted years`,
  recalculated in the browser whenever the threshold changes.
- **Threshold:** Rainfall and Wind use the **main trigger threshold**, so one entry
  updates both the trigger and the table. Rainfall compares the same mm (the trigger
  is Atlas 14's 24-hour depth, the table calendar-day totals). Wind converts the
  trigger's knots to m/s (× 0.514444). Flood uses a **flood-level menu**
  (action, minor, moderate, major) instead of a number: each gauge is compared with its
  own official stage for that level, since depth at the point has no conversion to a
  river level.

## Flood matched to flood stages (changed November 23, 2026)

The first version counted years whose peak **river flow** (cfs) reached a threshold that
started at the nearest gauge's median year, so rows showed 50–100% next to a 1% main
result: an ordinary high flow, not a flood. Flood rows now show how often each river
actually reached **flood stage**:

- **Gauges:** NOAA National Water Prediction Service river gauges within 100 km
  (`https://api.water.noaa.gov/nwps/v1/gauges?bbox…`) that have official flood stages
  (`/gauges/{lid}`: action, minor, moderate, major, in ft) and a linked USGS site.
  Reservoir and lake gauges without river-level history are not listed.
- **River levels:** USGS annual peak file (`nwis/peak … format=rdb`): per water year,
  the higher of `gage_ht` and `ag_gage_ht`.
- **Reference-height check:** NWS historic crests and USGS levels on the same date
  should agree. A gap over 1 ft is flagged on the row (e.g. 2026-11-23: Comal River at
  New Braunfels 3.2 ft, Guadalupe River below Comal 9.2 ft), because part of the record may
  use a different reference height.
- USGS and NWPS downloads are retried up to 3 times (network errors were intermittent).

Downtown San Antonio, minor flood stage: San Antonio River at Mitchell St 9.1% of years,
Olmos Creek 34.5%, Salado Creek at NE Loop 410 21.1%. These describe flooding near each
gauge. The main 1% is the chance of the selected water depth at the point itself (TWDB).
Minor flood stage is a much lower bar than a 100-year flood. Some NWS minor stages are
low (San Antonio River at Loop 410 reached its minor stage every year), and that is reported as is.

## Wind matched to the main curve (changed November 23, 2026)

The first version compared 5-second gusts from any storm (`WSF5`, 1996–2025) with the
main curve's hurricane-only sustained wind, which made the stations show 100% at low
speeds (thunderstorms). Wind rows now answer the main curve's question at each station:

- **Same storms:** a day is used only if a tropical cyclone's recorded 34 kn wind area
  covered the *station* that day, using the main curve's rule and data
  (`disaster_data.cyclone_footprint_days`, NOAA HURDAT2 quadrant radii, UTC dates).
- **Same kind of wind:** the station's fastest 2-minute wind (`WSF2`), close to the
  1-minute sustained wind in HURDAT2 (usually a few percent lower). Gusts are not used.
- **Same years:** 2004–2025, like the main curve.
- A year with no storm day counts as 0. A year whose storm day has no reading is left
  out (instruments often fail in strong storms); the count is shown per station.

Houston, 2026-11-23: the main curve gives 31.8% of years at ≥ 34 kn (7 of 22). The
storm wind areas also covered each nearby airport in 6–7 years, but measured sustained
wind reached 34 kn in only 4.5–18.2% of years, and 50 kn in 0–4.8%. The storm wind areas
mark the furthest reach of 34 kn winds, often over open water. Winds measured on land
are usually lower. Inland central Texas shows 0% at the stations and in the main curve.

## Interpretation limits

- Rainfall: calendar-day totals with station observing-day boundaries, not Atlas 14's
  24-hour design depths. A storm split across two days is undercounted.
- Wind: see "Wind matched to the main curve" below.
- Flood: how often the river reached flood stage near the gauge, not water depth at the selected point.
- All: observed history, not calibrated future probabilities. Station siting,
  instrument changes and (for flood) regulation or urbanization affect comparability.

## Page layout (November 22, 2026)

The viewer is ordered by the questions a user asks:

1. **Choose a location** — address search, Point/Area, map.
2. **Set your trigger and see the chance** — hazard, threshold, payout and result.
3. **Where the number comes from** (collapsed) — main curve, source, method, heat annual maxima.
4. **What nearby monitors show** — "Stations (n)" and one shared table for Heat,
   Rainfall, Wind and Flood. The first row repeats the main result at the point;
   numbered teal map pins match the rows; each row expands to that monitor's full
   curve. The live monitoring-network list (purple markers) is a collapsed sub-section.
5. **More context** (collapsed) — current heat readings, satellite fire detections,
   flood depth and damage table.

Wildfire's nearby-fire panel stays directly under the trigger because it is that
hazard's main result. Earthquake and wildfire have no monitor table.

## Interface

`GET /api/station-records?kind=rainfall|wind|flood&lat=…&lon=…&n=1–50`
(`station_records.py`). This viewer route is not part of the public `/api/v1`
contract. NOAA station files are shared with historical heat via
`historical_heat.ghcn_index` and `historical_heat.station_text` caches.
