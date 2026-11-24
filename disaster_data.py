"""Government data for NDApp's interactive disaster viewer (not binding quotes)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
from functools import lru_cache
import io
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode
import urllib.request

from hazard_engine import ModelUnavailable, coordinates
from hazard_providers import USGSPGAProvider, fetch_json

ROOT = Path(__file__).resolve().parent
FIRE_SERVER = "https://imagery.geoplatform.gov/iipp/rest/services/Fire_Aviation/"
FIRE_CLASS = FIRE_SERVER + "USFS_EDW_RMRS_WildfireHazardPotentialClassified/ImageServer"
FIRE_SOURCE = "https://www.fs.usda.gov/rds/archive/Catalog/RDS-2016-0034-3"
NHC_SOURCE = "https://www.nhc.noaa.gov/data/"


@lru_cache(maxsize=32)
def government_text(url: str, day: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp-government-hazards/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(20_000_001)
    if len(raw) > 20_000_000:
        raise ModelUnavailable("Government response exceeds the viewer size limit")
    return raw.decode("utf-8-sig")


def today_key():
    return int(datetime.now(timezone.utc).timestamp() // 86400)


def base(peril, metric, unit, source_name, source_url):
    return {"peril": peril, "metric": metric, "unit": unit, "source_name": source_name,
            "source_url": source_url, "frequency_label": "Annual exceedance probability (%)",
            "points": [], "details": [], "notes": [], "status": "available"}


def earthquake(lat, lon, site_class):
    if 24.4 <= lat <= 49.55 and -125 <= lon <= -66.75:
        model = "STATIC_CONUS_2023_R2"
    elif 50 <= lat <= 72 and (lon <= -129 or lon >= 170):
        model = "STATIC_ALASKA_2023_R1"
    elif 18 <= lat <= 23 and -161 <= lon <= -154:
        model = "STATIC_HAWAII_2023_R1"
    elif 17 <= lat <= 20 and -69 <= lon <= -63:
        model = "STATIC_PRVI_2025_R1"
    else:
        raise ModelUnavailable("This point is outside the supported USGS NSHM regional bounds.")
    _, doc = USGSPGAProvider(model, site_class).curve_document_at(lat, lon)
    result = base("earthquake", "Peak ground acceleration", "%g", "USGS National Seismic Hazard Model", doc["source"]["endpoint"])
    result["points"] = [{"frequency_pct": p["annual_exceedance_probability"] * 100,
                         "severity": p["intensity"], "annual_rate": p["frequency"]}
                        for p in doc["representation"]["points"]]
    result["details"] = [["Model", model], ["Site class", site_class],
                         ["Intensity", "Site PGA, percent of gravitational acceleration"],
                         ["Probability", "1 − exp(−annual USGS exceedance rate)"]]
    result["notes"] = ["Site class is an explicit assumption; select a class appropriate to the site.",
                       "These are modeled long-term shaking probabilities, not a live earthquake reading."]
    return result


def rainfall(lat, lon):
    url = "https://hdsc.nws.noaa.gov/cgi-bin/hdsc/new/fe_text_mean.csv?" + urlencode({
        "lat": lat, "lon": lon, "data": "depth", "units": "metric", "series": "ams"})
    raw = government_text(url, today_key())
    if "(millimeters)" not in raw or "Time series type: Annual maximum" not in raw:
        raise ModelUnavailable("NOAA Atlas 14 did not return annual-maximum rainfall depths for this point.")
    rows = list(csv.reader(io.StringIO(raw)))
    header = next((row for row in rows if row and row[0].startswith("by duration for AEP")), None)
    values = next((row for row in rows if row and row[0].strip() == "24-hr:"), None)
    if not header or not values or len(header) != len(values):
        raise ModelUnavailable("NOAA has no compatible 24-hour precipitation table at this point.")
    result = base("rainfall", "Maximum 24-hour rainfall", "mm", "NOAA Atlas 14", url)
    for fraction, value in zip(header[1:], values[1:]):
        match = re.fullmatch(r"\s*'?1/(\d+)\s*", fraction)
        if not match:
            raise ModelUnavailable("Unexpected NOAA annual-probability headings")
        depth = float(value)
        if not math.isfinite(depth) or depth < 0:
            raise ModelUnavailable("Invalid NOAA rainfall depth")
        period = int(match.group(1))
        result["points"].append({"frequency_pct": 100 / period, "severity": depth, "return_period": period})
    version = next((line for line in raw.splitlines() if line.startswith("NOAA Atlas")), "NOAA Atlas 14")
    result["details"] = [["Model", version], ["Duration", "24 hours"],
                         ["Series", "Annual maximum (AMS)"], ["Intensity", "Accumulation depth in millimeters"]]
    result["notes"] = ["Point precipitation-frequency estimates under the Atlas 14 stationary historical-climate model.",
                       "Rainfall is a separate disaster selection. Tropical-cyclone wind is shown under Wind."]
    return result


def flood(lat, lon):
    from server import lookup, SOURCE_URL
    sample = lookup(lat, lon)
    result = base("flood", "Peak inundation depth", "m", "TWDB 2025 flood-depth model", SOURCE_URL)
    labels = {"sampled": "Modeled depth", "no_mapped_depth": "No mapped depth / NoData",
              "not_installed": "Tile not installed", "read_error": "Raster read error"}
    result["points"] = [{"frequency_pct": float(chance),
                         "severity": entry["depth_ft"] * .3048 if entry["status"] == "sampled" else None,
                         "status": labels[entry["status"]]} for chance, entry in sample["events"].items()]
    result["details"] = [["Model", "2025 existing conditions, scenario 5"], ["Tile", sample["tile"]],
                         ["Coverage", "Installed Texas tiles"], ["Intensity", "Depth above ground, meters"]]
    result["notes"] = ["Missing or zero/NoData raster cells remain blank; they do not establish no flood risk.",
                       "FEMA flood zones are regulatory context; TWDB supplies the frequency-depth points."]
    if not any(p["severity"] is not None for p in result["points"]):
        result["status"] = "unavailable"
        result["reason"] = "No mapped depths for this point in the installed Texas model."
    return result


def fire_class(lat, lon):
    params = urlencode({"geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
                        "geometryType": "esriGeometryPoint", "returnFirstValueOnly": "true", "f": "json"})
    data, _ = fetch_json(FIRE_CLASS + "/getSamples?" + params)
    samples = data.get("samples", [])
    if not samples:
        return "No mapped class"
    classes = {1: "Very low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very high", 6: "Non-burnable", 7: "Water"}
    try:
        return classes.get(int(samples[0]["value"]), "No mapped class")
    except (ValueError, KeyError):
        return "No mapped class"


def wildfire(lat, lon):
    import rasterio
    from rasterio.warp import transform
    region = "HI" if 18 <= lat <= 23 and -161 <= lon <= -154 else "AK" if lat >= 50 else "CONUS"
    result = base("wildfire", "Flame length", "m", "USDA Forest Service FSim, third edition", FIRE_SOURCE)
    try:
        whp = fire_class(lat, lon)
    except (OSError, ValueError):
        whp = "WHP service unavailable"
    result["details"] = [["Model", "FSim LF2020, 270 m source rasters"], ["WHP class", whp],
                         ["Intensity", "Flame length at 8- and 12-foot thresholds"]]
    values = {}
    for name in ("BP", "FLP5", "FLP6"):
        path = ROOT / "data" / "usfs" / f"{region}_{name}.tif"
        if not path.is_file():
            result.update(status="unavailable", reason=f"Original USFS {region} rasters are not installed. Run python prepare_fire_data.py --region {region}.")
            return result
        with rasterio.open(path) as raster:
            xs, ys = transform("EPSG:4326", raster.crs, [lon], [lat])
            x, y = xs[0], ys[0]
            if not raster.bounds.left <= x < raster.bounds.right or not raster.bounds.bottom <= y < raster.bounds.top:
                raise ModelUnavailable("Point is outside the USFS source raster")
            value = next(raster.sample([(x, y)], masked=True))[0]
            if getattr(value, "mask", False):
                result.update(status="unavailable", reason="USFS has no modeled flame-length/probability cell at this point.")
                return result
            number = float(value)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ModelUnavailable("USFS source pixel is not a probability fraction")
            values[name] = number
    bp, p5, p6 = values["BP"], values["FLP5"], values["FLP6"]
    if p5 + p6 > 1 + 1e-6:
        raise ModelUnavailable("Incompatible USFS conditional intensity probabilities")
    result["points"] = [{"frequency_pct": bp * min(1, p5 + p6) * 100, "severity": 8 * .3048},
                        {"frequency_pct": bp * p6 * 100, "severity": 12 * .3048}]
    result["details"].append(["Annual burn probability", f"{bp * 100:.6g}%"])
    result["notes"] = ["Research annual exceedance estimate = annual burn probability × conditional flame-length class probability. Only the two supported thresholds are plotted.",
                       "Uses original quantitative GeoTIFFs, not rescaled visualization pixels. Landscape conditions reflect the model's 2020 inputs.",
                       "WHP is contextual; a non-burnable class or modeled zero does not establish no property/ember exposure."]
    result["frequency_label"] = "Estimated annual exceedance probability (%)"
    return result


@lru_cache(maxsize=2)
def cyclone_catalog(day):
    page = government_text(NHC_SOURCE, day)
    paths = re.findall(r'href="(/data/hurdat/hurdat2[^" ]+\.txt)"', page)
    atlantic = next((p for p in paths if re.search(r"hurdat2-1851-\d{4}-", p)), None)
    pacific = next((p for p in paths if "hurdat2-nepac-1949-" in p), None)
    if not atlantic or not pacific:
        raise ModelUnavailable("NOAA did not publish both expected HURDAT2 basin links")
    urls = ["https://www.nhc.noaa.gov" + p for p in [atlantic, pacific]]
    end = min(int(re.search(r"-(\d{4})-\d+\.txt", p).group(1)) for p in [atlantic, pacific])
    end = min(end, datetime.now(timezone.utc).year - 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        texts = list(pool.map(lambda url: government_text(url, day), urls))
    records = []
    for raw in texts:
        event_id = ""
        for row in csv.reader(io.StringIO(raw)):
            row = [v.strip() for v in row]
            if not row:
                continue
            if re.fullmatch(r"(?:AL|EP|CP)\d{6}", row[0]):
                event_id = row[0]
                continue
            if len(row) < 20 or not re.fullmatch(r"\d{8}", row[0]):
                continue
            year = int(row[0][:4])
            if not 2004 <= year <= end or row[3] not in {"TS", "HU", "SS"}:
                continue
            lat = float(row[4][:-1]) * (-1 if row[4][-1] == "S" else 1)
            lon = float(row[5][:-1]) * (-1 if row[5][-1] == "W" else 1)
            # Last field: best-track date (YYYYMMDD, UTC), used for station storm-day matching.
            records.append((event_id, year, lat, lon, int(row[6]), tuple(int(v) for v in row[8:20]), row[0]))
    if not records or end < 2004:
        raise ModelUnavailable("NOAA wind-radius records are unavailable")
    return records, end, urls


def distance_bearing(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dl / 2) ** 2
    distance_nm = 3440.065 * 2 * math.asin(min(1, math.sqrt(h)))
    bearing = math.degrees(math.atan2(math.sin(dl) * math.cos(b),
                          math.cos(a) * math.sin(b) - math.sin(a) * math.cos(b) * math.cos(dl))) % 360
    return distance_nm, bearing


def cyclone_footprint_days(lat, lon, threshold=34):
    """UTC dates when (lat, lon) was inside a tropical cyclone's recorded wind radius.

    Same rule as cyclone(): a best-track record with wind ≥ threshold whose quadrant
    radius for that threshold reaches the point. Used to restrict station wind records
    to tropical-storm days. Returns (set of ISO dates, last complete year).
    """
    records, end, _ = cyclone_catalog(today_key())
    offset = {34: 0, 50: 1, 64: 2}[threshold]
    days = set()
    for _event, _year, track_lat, track_lon, wind, radii, *rest in records:
        if wind < threshold or not rest:
            continue
        distance, bearing = distance_bearing(track_lat, track_lon, lat, lon)
        radius = radii[offset * 4 + min(3, int(bearing // 90))]
        if radius > 0 and distance <= radius:
            stamp = rest[0]
            days.add(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}")
    return days, end


def cyclone(lat, lon):
    if not 0 <= lat <= 65 or not -180 <= lon <= 0:
        raise ModelUnavailable("NOAA HURDAT2 covers Atlantic and northeast/central Pacific cyclones; this point is outside the supported viewer region.")
    records, end, urls = cyclone_catalog(today_key())
    hits = {34: set(), 50: set(), 64: set()}
    events = set()
    missing = 0
    for event, year, track_lat, track_lon, wind, radii, *_ in records:
        distance, bearing = distance_bearing(track_lat, track_lon, lat, lon)
        quadrant = min(3, int(bearing // 90))  # NOAA order: NE, SE, SW, NW
        for offset, threshold in enumerate(hits):
            radius = radii[offset * 4 + quadrant]
            if wind >= threshold and radius < 0:
                missing += 1
            if wind >= threshold and radius > 0 and distance <= radius:
                hits[threshold].add(year)
                events.add(event)
    years = end - 2004 + 1
    result = base("cyclone", "Maximum sustained cyclone wind", "kn", "NOAA NHC HURDAT2 wind footprints", NHC_SOURCE)
    result["frequency_label"] = "Observed annual exceedance frequency (%)"
    result["points"] = [{"frequency_pct": len(hit_years) / years * 100, "severity": threshold,
                         "hit_years": len(hit_years), "exposure_years": years} for threshold, hit_years in hits.items()]
    result["details"] = [["Record", f"2004–{end} ({years} complete calendar years)"],
                         ["Metric", "Sustained wind, 34 / 50 / 64 knots"],
                         ["Method", "Selected point inside NOAA quadrant wind radii at best-track observation times"],
                         ["Cyclones with sampled footprint at point", str(len(events))]]
    result["notes"] = ["Historical fractions of years with a recorded cyclone wind footprint at the point. These are lower-bound observed frequencies, not calibrated future probabilities or return-period wind gusts.",
                       "No interpolation between best-track times; between-record passages and missing wind radii can be missed. Zero observed years is not zero risk.",
                       "Wind covers tropical cyclones only, not thunderstorms, tornadoes, other windstorms or peak gusts. Rainfall uses a separate 24-hour accumulation curve."]
    result["source_files"] = urls
    result["missing_radius_records"] = missing
    return result


def disaster_at(peril, lat, lon, site_class="BC", radius_km=None):
    coordinates(lat, lon)
    from fireline_intensity import fireline_intensity
    from historical_heat import heat_at
    from potential_fire import potential_fire_at
    from satellite_fire_history import DEFAULT_RADIUS_KM, satellite_fire_curve
    handlers = {
                "satellite_fire": lambda: satellite_fire_curve(lat, lon, radius_km or DEFAULT_RADIUS_KM),"earthquake": lambda: earthquake(lat, lon, site_class), "flood": lambda: flood(lat, lon),
                "wildfire": lambda: wildfire(lat, lon), "cyclone": lambda: cyclone(lat, lon),
                "fireline_intensity": lambda: fireline_intensity(lat, lon),
                "potential_fire_intensity": lambda: potential_fire_at(lat, lon),
                "heat": lambda: heat_at(lat, lon),
                "rainfall": lambda: rainfall(lat, lon)}
    if peril not in handlers:
        raise ValueError("Unknown disaster")
    try:
        result = handlers[peril]()
    except ModelUnavailable as exc:
        unavailable = {"peril": peril, "status": "unavailable", "reason": str(exc), "points": [],
                       "latitude": lat, "longitude": lon}
        if peril == "fireline_intensity":
            from fireline_intensity import NO_PROBABILITY
            unavailable.update(hazard_kind="scenario_intensity", annual_probability_available=False,
                               trigger_unavailable_reason=NO_PROBABILITY)
        if peril == "potential_fire_intensity":
            unavailable.update(hazard_kind="potential_intensity_exceedance", annual_probability_available=False,
                               actual_wildfire_probability_available=False, trigger_unavailable_reason=str(exc))
        return unavailable
    result.update(latitude=lat, longitude=lon)
    result["points"].sort(key=lambda p: p["frequency_pct"])
    return result
