"""Satellite active-fire observations near a point: NASA FIRMS VIIRS, last ~1–8 days.

A detection is a thermal anomaly seen in a ~375 m pixel at an overpass. It is
not a confirmed wildfire (prescribed burns, flares and industry also appear), and
no detection does not mean no fire (clouds, small or short fires can be missed).
FRP (MW) is radiative power at observation, never fireline intensity (kW/m).
Design: docs/nasa-data.md section 7.
"""
import csv
from datetime import datetime, timedelta, timezone
import io
import math
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from hazard_engine import coordinates

HOST = "firms.modaps.eosdis.nasa.gov"
# FIRMS publishes the same rolling US files in three lengths; all checked live on 2026-11-10.
# Files hold whole UTC days, so each reaches back further than its name (checked
# 2026-11-22: "24h" up to ~46 h old, "48h" ~71 h, "7d" ~196 h).
WINDOWS = {"24h": "about the last 1–2 days", "48h": "about the last 2–3 days", "7d": "about the last 7–8 days"}
DEFAULT_WINDOW = "24h"
_FILES = {"NOAA-20": ("noaa-20-viirs-c2", "J1"), "NOAA-21": ("noaa-21-viirs-c2", "J2")}


def source_url(satellite, window):
    folder, prefix = _FILES[satellite]
    return f"https://{HOST}/data/active_fire/{folder}/csv/{prefix}_VIIRS_C2_USA_contiguous_and_Hawaii_{window}.csv"


SOURCES = {satellite: source_url(satellite, DEFAULT_WINDOW) for satellite in _FILES}
CACHE_SECONDS = 600
MAX_BYTES = 20 * 1024 * 1024
DEFAULT_RADIUS_KM, MAX_RADIUS_KM, MAX_RETURNED = 25, 100, 200
CONFIDENCE = {"l": "low", "n": "nominal", "h": "high", "low": "low", "nominal": "nominal", "high": "high"}
# Published files cover the contiguous US and Hawaii only (approximate boxes).
COVERAGE = ((24.0, 49.6, -125.0, -66.5), (18.5, 22.5, -161.0, -154.5))
NOTES = [
    "A detection is a thermal anomaly in a ~375 m satellite pixel at overpass time, not a confirmed wildfire.",
    "Prescribed and agricultural burns, gas flares and industrial heat can also be detected.",
    "No detection does not mean no fire: clouds, smoke, small, cool or short-lived fires and gaps between overpasses can be missed.",
    "Coordinates are pixel centers, not ignition points or fire perimeters; several pixels can belong to one fire.",
    "Older detections in the rolling files can be revised when NASA reprocesses them.",
    "Fire radiative power (MW) is the radiative energy rate at observation. It is not fireline intensity (kW/m) and is never summed across overpasses.",
    "Near-real-time data can be revised; check agency incident reports and perimeters to confirm a fire.",
]
_cache, _lock = {}, threading.Lock()


def _distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def in_coverage(lat, lon, radius_km):
    """True when the whole search circle lies inside a published coverage box."""
    dlat = radius_km / 111.2
    dlon = radius_km / (111.2 * max(0.01, math.cos(math.radians(lat))))
    return any(s <= lat - dlat and lat + dlat <= n and w <= lon - dlon and lon + dlon <= e
               for s, n, w, e in COVERAGE)


class _NasaOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https" or urlsplit(newurl).netloc != HOST:
            raise OSError("Unexpected FIRMS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp-satellite-fire/1.0", "Accept-Encoding": "identity"})
    with urllib.request.build_opener(_NasaOnly()).open(request, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise OSError("FIRMS file exceeded size limit")
    return body.decode("utf-8")


def _parse_time(row):
    date, clock = row.get("acq_date", ""), str(row.get("acq_time", "")).zfill(4)
    try:
        return datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise OSError("Invalid FIRMS acquisition time") from exc


def _number(row, key, required=True):
    value = row.get(key, "")
    if value in ("", None):
        if required:
            raise OSError(f"Missing FIRMS {key}")
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise OSError(f"Invalid FIRMS {key}") from exc
    if not math.isfinite(number):
        raise OSError(f"Invalid FIRMS {key}")
    return number


def _parse(text, satellite, now):
    rows = list(csv.DictReader(io.StringIO(text)))
    required = {"latitude", "longitude", "acq_date", "acq_time", "confidence", "frp", "daynight", "scan", "track"}
    if rows and not required <= set(rows[0]):
        raise OSError("FIRMS file is missing expected columns")
    detections = []
    for row in rows:
        lat, lon = _number(row, "latitude"), _number(row, "longitude")
        coordinates(lat, lon)
        acquired = _parse_time(row)
        if acquired > now + timedelta(minutes=10):
            raise OSError("FIRMS acquisition time is in the future")
        confidence = CONFIDENCE.get(str(row["confidence"]).strip().lower())
        if confidence is None:
            raise OSError("Unknown FIRMS confidence value")
        detections.append({"satellite": satellite, "instrument": "VIIRS", "latitude": lat, "longitude": lon,
                           "acquired_at": acquired.isoformat(), "confidence": confidence,
                           "frp_mw": _number(row, "frp", required=False),
                           "daynight": {"D": "day", "N": "night"}.get(row["daynight"], None),
                           "pixel_scan_km": _number(row, "scan", required=False),
                           "pixel_track_km": _number(row, "track", required=False),
                           "brightness_ti4_k": _number(row, "bright_ti4", required=False),
                           "version": row.get("version")})
    return detections


def _source(satellite, url, now):
    with _lock:
        cached = _cache.get(url)
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
    detections = _parse(_download(url), satellite, now)
    times = [d["acquired_at"] for d in detections]
    entry = {"satellite": satellite, "status": "ok", "url": url, "retrieved_at": now.isoformat(),
             "file_detection_count": len(detections), "oldest_acquired_at": min(times, default=None),
             "newest_acquired_at": max(times, default=None), "detections": detections}
    with _lock:
        _cache[url] = (time.monotonic(), entry)
    return entry


FRP_CURVE_POINTS = 50
FRP_BASIS = ("Share of this window's detections within the radius whose FRP is at or above each value. It is conditional "
             "on a detection, counts every overpass of a burning fire separately, and is not an annual probability, "
             "a fire-intensity exceedance or the chance of fire at this location.")


def frp_summary(detections):
    """Empirical FRP (MW) distribution of the detections: an exceedance share, not an annual probability."""
    values = sorted((d["frp_mw"] for d in detections if d.get("frp_mw") is not None), reverse=True)
    n = len(values)
    summary = {"unit": "MW", "detections_with_frp": n, "detections_without_frp": len(detections) - n,
               "minimum_mw": values[-1] if n else None, "maximum_mw": values[0] if n else None,
               "median_mw": round((values[(n - 1) // 2] + values[n // 2]) / 2, 3) if n else None,
               "basis": FRP_BASIS, "exceedance": []}
    # One point per distinct value (count of detections at or above it), largest first.
    distinct = []
    for index, value in enumerate(values):
        if index + 1 == n or values[index + 1] != value:
            distinct.append((value, index + 1))
    if len(distinct) > FRP_CURVE_POINTS:
        step = (len(distinct) - 1) / (FRP_CURVE_POINTS - 1)
        distinct = [distinct[round(i * step)] for i in range(FRP_CURVE_POINTS)]
    summary["exceedance"] = [{"frp_mw": value, "detections_at_or_above": count,
                              "share_pct": round(100 * count / n, 4)} for value, count in distinct]
    return summary


def satellite_fire_detections(lat, lon, radius_km=DEFAULT_RADIUS_KM, window=DEFAULT_WINDOW):
    coordinates(lat, lon)
    if isinstance(radius_km, bool) or not isinstance(radius_km, (int, float)) or not 1 <= radius_km <= MAX_RADIUS_KM:
        raise ValueError(f"radius_km must be between 1 and {MAX_RADIUS_KM}.")
    if window not in WINDOWS:
        raise ValueError("window must be 24h, 48h or 7d.")
    now = datetime.now(timezone.utc)
    base = {"latitude": lat, "longitude": lon, "radius_km": radius_km, "window": window,
            "window_description": f"Rolling FIRMS files covering {WINDOWS[window]}; each source lists its oldest and newest detection time",
            "source_name": "NASA FIRMS active fire (VIIRS 375 m, NOAA-20 and NOAA-21)",
            "source_url": f"https://{HOST}/", "checked_at": now.isoformat(),
            "notes": NOTES + (["Longer windows include more prescribed and agricultural burns, and one fire burning for days "
                               "appears on many overpasses; those repeats are not separate fires."] if window != "24h" else [])}
    if not in_coverage(lat, lon, radius_km):
        return {**base, "status": "unavailable", "detection_count": 0, "returned_count": 0, "detections": [],
                "sources": [], "reason": "These FIRMS files cover the contiguous US and Hawaii; this search area is "
                                         "outside or crosses that coverage, so no result is substituted."}
    sources, found = [], []
    for satellite in _FILES:
        url = source_url(satellite, window)
        try:
            entry = _source(satellite, url, now)
        except (OSError, ValueError, UnicodeError) as exc:
            sources.append({"satellite": satellite, "status": "failed", "url": url, "error": type(exc).__name__})
            continue
        sources.append({key: value for key, value in entry.items() if key != "detections"})
        for detection in entry["detections"]:
            distance = _distance_km(lat, lon, detection["latitude"], detection["longitude"])
            if distance <= radius_km:
                age = (now - datetime.fromisoformat(detection["acquired_at"])).total_seconds() / 3600
                found.append({**detection, "distance_km": round(distance, 2), "age_hours": round(max(0.0, age), 1)})
    if all(source["status"] == "failed" for source in sources):
        raise OSError("All FIRMS satellite sources failed")
    found.sort(key=lambda d: (d["distance_km"], d["acquired_at"], d["satellite"]))
    failed = [s["satellite"] for s in sources if s["status"] == "failed"]
    return {**base, "status": "available" if found else "not_found", "detection_count": len(found),
            "returned_count": min(len(found), MAX_RETURNED), "detections": found[:MAX_RETURNED],
            "nearest_km": found[0]["distance_km"] if found else None,
            "newest_age_hours": min((d["age_hours"] for d in found), default=None),
            "frp_summary": frp_summary(found),
            "sources": sources,
            "reason": (f"Partial: {', '.join(failed)} could not be retrieved; detections from other satellites shown. " if failed else "")
                      + ("" if found else "No satellite detections in this window and radius. This does not mean there is no fire.")
                      or None}
