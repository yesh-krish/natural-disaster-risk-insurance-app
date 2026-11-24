"""Annual satellite fire-detection exceedance from the NASA FIRMS VIIRS S-NPP archive.

For a point and radius: the share of complete years 2013–2025 with at least one
presumed vegetation-fire detection (FIRMS type 0, nominal/high confidence) whose
fire radiative power (FRP, MW) is at or above a threshold. Each year counts once.

Data: data/firms_viirs_snpp, built by prepare_satellite_fire_data.py. Only S-NPP
is used because it is the one VIIRS satellite flying the whole period; adding
NOAA-20 (2018) and NOAA-21 (2023) would raise later years' detection chances.
Each satellite is also a "monitor" with its own curve (satellite_monitor_records):
S-NPP and NOAA-20 VIIRS, Terra and Aqua MODIS, each from its own archive folder.
See docs/satellite-fire-exceedance.md.
"""
from datetime import date, timedelta
from functools import lru_cache
import json
import math
from pathlib import Path

import numpy as np

from hazard_engine import ModelUnavailable, coordinates

DATA = Path(__file__).resolve().parent / "data" / "firms_viirs_snpp"
FIRST_YEAR, LAST_YEAR = 2013, 2025
RADII_KM = (5, 10, 25, 50, 100)  # suggested presets; any radius in the range below is accepted
# 0.5 km floor: detections are 375 m pixel centres, so smaller circles mostly test pixel placement.
MIN_RADIUS_KM, MAX_RADIUS_KM = 0.5, 100
DEFAULT_RADIUS_KM = 25
# Satellite rows: the main curve itself rests on 13 S-NPP years, so the station tables'
# 20-year rule would call its own source unreliable. Fewer than 10 years is labelled short.
SATELLITE_MIN_YEARS = 10
EARTH_KM = 6371.0088
SOURCE_URL = "https://firms.modaps.eosdis.nasa.gov/country/"
# The yearly "United States" files cover the 50 states (approximate boxes).
COVERAGE = ((24.0, 49.6, -125.0, -66.5), (18.5, 22.5, -161.0, -154.5), (51.0, 71.6, -180.0, -129.0))


# Satellites with yearly archives ("monitors"), in table order. Complete calendar years only.
MONITORS = {
    "viirs_snpp": {"folder": "firms_viirs_snpp", "years": (2013, 2025), "name": "Suomi NPP VIIRS",
                   "pixel": "375 m", "overpass": "about 1:30 am and 1:30 pm"},
    "viirs_noaa20": {"folder": "firms_viirs_noaa20", "years": (2019, 2025), "name": "NOAA-20 VIIRS",
                     "pixel": "375 m", "overpass": "about 50 minutes after Suomi NPP"},
    "modis_terra": {"folder": "firms_modis_terra", "years": (2001, 2025), "name": "Terra MODIS",
                    "pixel": "1 km", "overpass": "about 10:30 am and 10:30 pm"},
    "modis_aqua": {"folder": "firms_modis_aqua", "years": (2003, 2025), "name": "Aqua MODIS",
                   "pixel": "1 km", "overpass": "about 1:30 pm and 1:30 am"},
}


def _folder(key):
    # DATA is the S-NPP folder; the other satellites sit beside it (tests patch DATA).
    return DATA if key == "viirs_snpp" else DATA.parent / MONITORS[key]["folder"]


@lru_cache(maxsize=4)
def load_archive(key="viirs_snpp"):
    first, last = MONITORS[key]["years"]
    command = f"python prepare_satellite_fire_data.py --satellites {key}"
    manifest_path = _folder(key) / "manifest.json"
    if not manifest_path.exists():
        raise ModelUnavailable(f"The {MONITORS[key]['name']} fire archive is not installed. Run: {command}")
    manifest = json.loads(manifest_path.read_text())
    years = {}
    for year in range(first, last + 1):
        path = _folder(key) / f"{year}.npz"
        if str(year) not in manifest.get("years", {}) or not path.exists():
            raise ModelUnavailable(f"The {MONITORS[key]['name']} fire archive is missing {year}. Run: {command}")
        with np.load(path) as arrays:
            years[year] = {name: arrays[name] for name in ("latitude", "longitude", "frp", "day_of_year")}
    return years, manifest


def _within(arrays, lat, lon, radius_km):
    """Boolean mask of detections within radius_km (bounding-box prefilter, then haversine)."""
    dlat = math.degrees(radius_km / EARTH_KM)
    dlon = dlat / max(0.01, math.cos(math.radians(min(89.0, abs(lat) + dlat))))
    box = (np.abs(arrays["latitude"] - lat) <= dlat) & (np.abs(((arrays["longitude"] - lon + 180) % 360) - 180) <= dlon)
    index = np.nonzero(box)[0]
    p1, p2 = math.radians(lat), np.radians(arrays["latitude"][index].astype(np.float64))
    dl = np.radians(arrays["longitude"][index].astype(np.float64) - lon)
    h = np.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    distance = 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    return index[distance <= radius_km]


def valid_radius(value):
    """Any search radius from 0.5 to 100 km; returns it as a float (whole numbers stay int)."""
    if isinstance(value, bool):
        raise ValueError("radius_km must be a number.")
    radius = float(value)
    if not math.isfinite(radius) or not MIN_RADIUS_KM <= radius <= MAX_RADIUS_KM:
        raise ValueError(f"radius_km must be from {MIN_RADIUS_KM:g} to {MAX_RADIUS_KM:g} km.")
    return int(radius) if radius.is_integer() else radius


def _annual_rows(archive, lat, lon, radius_km):
    """Per year: detections within the radius and the strongest one (None when there were none)."""
    rows = []
    for year, arrays in archive.items():
        index = _within(arrays, lat, lon, radius_km)
        frp = arrays["frp"][index]
        if len(frp):
            best = int(np.argmax(frp))
            day = int(arrays["day_of_year"][index][best])
            rows.append({"year": year, "detections": int(len(frp)), "max_frp_mw": round(float(frp[best]), 2),
                         "max_date": (date(year, 1, 1) + timedelta(days=day - 1)).isoformat()})
        else:
            rows.append({"year": year, "detections": 0, "max_frp_mw": None, "max_date": None})
    return rows


def _check_point(lat, lon):
    coordinates(lat, lon)
    if not any(south <= lat <= north and west <= lon <= east for south, north, west, east in COVERAGE):
        raise ModelUnavailable("NASA's yearly FIRMS archive used here covers the United States only.")


def satellite_monitor_records(lat, lon, radius_km=DEFAULT_RADIUS_KM):
    """One row per satellite: its own share of years with a detection at or above a threshold.

    Rows use the shared monitor-table shape (station_records). Annual maxima include
    0 for years with no detection, so those years stay in the denominator. Satellites
    are never combined. MODIS (1 km pixels) misses small fires that VIIRS (375 m) sees.
    """
    from station_records import _points
    radius_km = valid_radius(radius_km)
    _check_point(lat, lon)
    rows = []
    for key, spec in MONITORS.items():
        row = {"id": key, "name": f"{spec['name']} ({spec['pixel']} pixels)", "latitude": None, "longitude": None,
               "distance_km": None, "url": SOURCE_URL, "overpass": spec["overpass"],
               "period": {"start": spec["years"][0], "end": spec["years"][1]}}
        try:
            archive, _ = load_archive(key)
        except ModelUnavailable as exc:
            row.update(status="source_unavailable", counted_years=None, annual_maxima=[], points=[], highest=None, median=None, reason=str(exc))
            rows.append(row)
            continue
        years = _annual_rows(archive, lat, lon, radius_km)
        row["detections"] = sum(year["detections"] for year in years)
        row["years_with_detection"] = sum(year["detections"] > 0 for year in years)
        maxima = [year["max_frp_mw"] or 0.0 for year in years]
        ordered = sorted(maxima)
        middle = len(ordered) // 2
        row.update(counted_years=len(maxima), annual_maxima=maxima, highest=max(maxima),
                   median=ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2)
        positive = [value for value in maxima if value > 0]
        if len(maxima) < SATELLITE_MIN_YEARS:
            row.update(status="insufficient_years", points=[],
                       reason=f"{len(maxima)} complete years; at least {SATELLITE_MIN_YEARS} are needed for a reliable share.")
        else:
            row.update(status="available", points=[p for p in _points(maxima) if p["severity"] > 0] if positive else [])
        rows.append(row)
    return {"kind": "satellite", "latitude": lat, "longitude": lon, "radius_km": radius_km, "unit": "MW",
            "metric": f"Fire intensity of each year's strongest detection within {radius_km:g} km (fire radiative power)",
            "source_name": "NASA FIRMS standard archive", "requested_stations": len(rows), "station_limit": len(rows),
            "stations_within_radius": len(rows), "minimum_years": SATELLITE_MIN_YEARS, "stations": rows,
            "year_rule": "Each complete calendar year counts once, at that satellite's strongest presumed vegetation-fire detection within the radius. Years with no detection count as below every threshold.",
            "notes": ["Each row is one satellite's own record. Satellites are not combined, and the main curve uses Suomi NPP only.",
                      "Satellites are not on one MW scale. MODIS pixels are 1 km, so MODIS misses small fires that VIIRS (375 m) sees, but one MODIS pixel can cover more of a large fire and report more MW. Satellites also pass at different times of day, catching fires at different stages.",
                      "NOAA-20 has 7 complete years (2019–2025), so its row is a short record. NOAA-21 has no yearly NASA archive yet.",
                      "Satellite detections only: crop and planned burns are included, and fires under cloud or between passes are missed."]}


def satellite_fire_curve(lat, lon, radius_km=DEFAULT_RADIUS_KM):
    coordinates(lat, lon)
    radius_km = valid_radius(radius_km)
    _check_point(lat, lon)
    archive, manifest = load_archive()
    rows = _annual_rows(archive, lat, lon, radius_km)
    total = len(rows)
    maxima = [row["max_frp_mw"] or 0.0 for row in rows]
    active_years = sum(value > 0 for value in maxima)
    result = {"peril": "satellite_fire", "status": "available", "radius_km": radius_km,
              "metric": f"Fire intensity of the strongest satellite detection within {radius_km:g} km (fire radiative power)", "unit": "MW",
              "source_name": "NASA FIRMS VIIRS S-NPP 375 m archive", "source_url": SOURCE_URL,
              "frequency_label": f"Share of years {FIRST_YEAR}–{LAST_YEAR} with a detection at or above this power (%)",
              "frequency_basis": "historical_annual_frequency",
              "record_period": {"start_year": FIRST_YEAR, "end_year": LAST_YEAR, "years": total},
              "annual_detections": rows, "points": [],
              "provenance": {"sources": [{"year": int(year), "url": entry["url"], "sha256": entry["sha256"], "fetched_at": entry["fetched_at"]}
                                         for year, entry in sorted(manifest.get("years", {}).items())]},
              "details": [["Search radius", f"{radius_km:g} km around the point"],
                          ["Years with any detection", f"{active_years} of {total} ({FIRST_YEAR}–{LAST_YEAR})"],
                          ["Detections counted", f"{sum(row['detections'] for row in rows):,} (presumed vegetation fires, nominal/high confidence)"],
                          ["Satellite", "Suomi NPP VIIRS only, 375 m pixels, about 1:30 am and 1:30 pm overpasses"],
                          ["Frequency", "100 × years whose strongest detection is at or above the threshold / all years"]],
              "notes": ["Satellite thermal detections only: no ground report confirms them. FIRMS type 0 means presumed vegetation fire; crop, pasture and planned burns are included and cannot be separated.",
                        "Fixed heat sources (gas flares, industry, volcanoes, offshore) and low-confidence pixels are removed using NASA's own classification.",
                        "Fire radiative power is the heat given off when the satellite passed, not the fire's final size. A large fire seen at a quiet moment can show a low value.",
                        "Clouds, smoke, short or small fires and the time between passes mean fires can be missed, so these frequencies are lower bounds.",
                        f"{total} years gives steps of about {100 / total:.1f} percentage points. Observed history, not a calibrated future probability; zero observed years does not mean zero future risk.",
                        "Only Suomi NPP is counted so every year has the same satellite coverage. Live detections in the panel below also use NOAA-20 and NOAA-21."]}
    positive = sorted({value for value in maxima if value > 0})
    if not positive:
        result.update(status="unavailable", reason=f"No presumed vegetation-fire detection within {radius_km:g} km in any year {FIRST_YEAR}–{LAST_YEAR}. Zero observed years does not mean zero future risk; try a larger radius.")
        return result
    result["points"] = [{"severity": threshold, "frequency_pct": 100 * sum(value >= threshold for value in maxima) / total,
                         "hit_years": sum(value >= threshold for value in maxima), "exposure_years": total}
                        for threshold in positive]
    return result
