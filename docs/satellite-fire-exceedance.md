# Satellite fire-detection exceedance (NASA FIRMS)

Added **November 22, 2026**. Hazard option: **Wildfire / Satellite fire detections (NASA)**.

## What it measures

For a point and any search radius from 0.5 to 100 km (presets 5, 10, 25, 50, 100;
custom values such as 12.5 km are accepted), the share of complete
years **2013–2025** with at least one satellite fire detection within the radius
whose **fire radiative power (FRP)** is at or above the trigger threshold (MW).
Each year counts once, at its strongest detection. With 13 years, each year is
worth about 7.7 percentage points.

This is satellite data only. No ground report confirms the detections. It
complements the ground-reported **Nearby fire frequency** option (USFS FPA FOD
fire sizes in acres).

## Source and filtering

| Item | Choice |
| --- | --- |
| Source | NASA FIRMS standard archive, yearly "United States" files: `https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/<year>/viirs-snpp_<year>_United_States.csv` (free, no key) |
| Satellite | Suomi NPP VIIRS (375 m) only. It is the one VIIRS satellite flying for the whole period; NOAA-20 (2018) and NOAA-21 (2023) would make later years more likely to have a detection. |
| Years | 2013–2025. S-NPP fire data begin 2012-01-20, so 2012 is not a complete year. |
| Kept | FIRMS `type` 0 (presumed vegetation fire) with nominal or high confidence |
| Removed | `type` 1 (volcano), 2 (other static land source: gas flares, industry), 3 (offshore); low-confidence pixels. Counts are recorded per year in the manifest. |

`prepare_satellite_fire_data.py` streams each yearly file (~35 MB), keeps the
filtered rows and writes `data/firms_viirs_snpp/<year>.npz` (latitude, longitude,
FRP, day of year) plus `manifest.json` with URL, SHA256, retrieval time and kept or
dropped counts. Built 2026-11-22: 5.2 million detections kept, about 1.1 million
static-source and 0.26 million low-confidence rows removed, 39.7 MB on disk.
**Deployment:** run the script once on the server (`data/` is not in Git). Without
it, the option reports that the archive is not installed.

## Calculation

`satellite_fire_history.satellite_fire_curve(lat, lon, radius_km)`: for each
year, a bounding-box prefilter then great-circle distance selects detections
within the radius; the year's maximum FRP is kept (0 with no detection). Curve
levels are the distinct positive annual maxima; frequency =
`100 × years with maximum ≥ level / 13`. Thresholds between levels are
interpolated and labelled; above the strongest observed detection the result is an
**upper bound**, and below the weakest a lower bound. Nothing is extrapolated,
as with Nearby fire frequency. A per-year table (detections, strongest detection,
date) is under "Where the number comes from". Area mode is not supported.

The radius floor is 0.5 km because detections are 375 m pixel centres; smaller circles
would mostly reflect where pixel centres fall. The map's radius box reloads the curve on
Enter or when you leave the box; an out-of-range value is flagged and not sent.

## Per-satellite monitor table (added the same day)

Section 4 lists each fire-sensing satellite as a monitor with its own record around
the point, using the main MW threshold and search radius:

| Satellite | Complete years | Pixel | Folder |
| --- | --- | --- | --- |
| Suomi NPP VIIRS | 2013–2025 (13) | 375 m | `data/firms_viirs_snpp` (also the main curve) |
| NOAA-20 VIIRS | 2019–2025 (7, short record) | 375 m | `data/firms_viirs_noaa20` (data begin 2018-04-01) |
| Terra MODIS | 2001–2025 (25) | 1 km | `data/firms_modis_terra` |
| Aqua MODIS | 2003–2025 (23) | 1 km | `data/firms_modis_aqua` (data begin 2002-07-04) |

NASA's yearly MODIS file holds both Terra and Aqua and is split by its `satellite`
column. MODIS confidence is numeric; values of 30 or more (nominal/high) are kept.
NOAA-21 has no yearly archive yet. Route: `GET /api/satellite-monitors?lat=…&lon=…&radius_km=…`
(`satellite_monitor_records`). Years without a detection count as 0, so they remain in
the denominator. Satellites are never combined.

A satellite row needs at least **10** complete years for a plain %. The station tables
use 20, but the main curve itself rests on 13 S-NPP years, so 20 would call its own
source unreliable. NOAA-20 (7 years) is labelled a short record.

**The satellites are not on one MW scale.** MODIS misses small fires, but one 1 km pixel
can cover more of a large fire and report more MW than a 375 m VIIRS pixel. Downtown
San Antonio, 25 km, 2026-11-22:

| Satellite | Years with a detection | Highest MW | Years ≥ 10 MW | Years ≥ 30 MW |
| --- | --- | --- | --- | --- |
| Suomi NPP VIIRS | 13 of 13 | 54.2 | 61.5% | 15.4% |
| NOAA-20 VIIRS (short) | 7 of 7 | 40.3 | 71.4% | 28.6% |
| Terra MODIS | 23 of 25 | 186.7 | 80.0% | 28.0% |
| Aqua MODIS | 19 of 23 | 276.1 | 52.2% | 30.4% |

Run `python prepare_satellite_fire_data.py` (all satellites, about 84 MB on disk) on a new server.

## Limits

- `type` 0 still includes crop, pasture and planned burns, which are common in Texas.
- FRP is the heat released at the overpass moment (S-NPP passes about 1:30 am and
  1:30 pm local), not the fire's final size or fireline intensity.
- Clouds, smoke, small, short or cool fires and gaps between passes cause misses,
  so frequencies are lower bounds. Pixel centres are not ignition points.
- Observed history, not a calibrated future probability. Zero observed years does
  not mean zero future risk.

## Live-detection window labels (fixed the same day)

NASA's rolling files hold whole UTC days, so each reaches back further than its
name. Measured 2026-11-22: the "24h" file reached about 46 h, "48h" about 71 h and
"7d" about 196 h. Labels now say "last 1–2 days", "2–3 days" and "7–8 days".
