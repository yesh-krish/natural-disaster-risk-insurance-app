"""Nearby station context, independent of the model used for hazard curves."""
import csv
import gzip
import io
import json
import math
import time
import re
import threading
import zlib
import urllib.request
from functools import lru_cache
from datetime import datetime, timezone
from urllib.parse import urlencode

from hazard_engine import coordinates
from heat_context import heat_context

RADIUS_KM = 200
SEARCH_RADII_KM = (20, 75, RADIUS_KM)
EARTH_KM = 6371.0088
NETWORKS = {
    "flood": "USGS active stream gauges with gauge-height records",
    "cyclone": "NOAA Aviation Weather Center METAR wind stations",
    "rainfall": "USGS active gauges with precipitation records",
    "earthquake": "EarthScope FDSN seismic stations with current operating epochs",
    "wildfire": "NIFC interagency RAWS fire-weather stations listed active",
}
SEISMIC_URL = "https://service.earthscope.org/fdsnws/station/1/query"
RAWS_URL = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/PublicView_RAWS/FeatureServer/1/query"

# The multi-station search deliberately starts with one explicitly named network.
AWC_NETWORK = "NOAA Aviation Weather Center METAR stations"
AWC_CATALOG = "https://aviationweather.gov/data/cache/stations.cache.json.gz"
MAX_STATIONS = 50
METAR_VARIABLES = ["Air temperature", "Dew point", "Wind speed and direction",
                   "Visibility", "Altimeter pressure", "Cloud cover and ceiling"]
_awc_cache = {}
_awc_lock = threading.Lock()
_awc_next_request = 0


def station_count(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or not 1 <= int(value) <= MAX_STATIONS:
        raise ValueError(f"n must be a positive integer from 1 to {MAX_STATIONS}.")
    return int(value)


def _awc_resource(url, ttl, compressed=False):
    """Coalesce concurrent requests, cache successes, and back off failures 60s."""
    global _awc_next_request
    with _awc_lock:
        now = time.monotonic()
        cached = _awc_cache.get(url)
        if cached and now < cached[0]:
            if cached[1] is None:
                raise OSError("NOAA service unavailable; retry after one minute.")
            return cached[1], cached[2]
        try:
            if now < _awc_next_request:
                time.sleep(_awc_next_request - now)
            # At most one upstream request per second in this server process.
            # The initial metadata and report requests must also be paced.
            _awc_next_request = time.monotonic() + 1
            request = urllib.request.Request(url, headers={"User-Agent": "NDApp/0.2 station-context research app"})
            with urllib.request.urlopen(request, timeout=6) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise OSError("NOAA response exceeds size limit")
            if compressed:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(16_000_001)
                if len(raw) > 16_000_000:
                    raise OSError("NOAA catalog exceeds size limit")
            payload = json.loads(raw) if raw.strip() else []  # HTTP 204: no reports.
            if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                raise ValueError("Unexpected NOAA response")
            if compressed and not payload:
                raise OSError("Empty NOAA station catalog")
        except (OSError, ValueError, EOFError, zlib.error) as exc:
            _awc_cache[url] = (time.monotonic() + 60, None, None)
            raise OSError("NOAA service unavailable; retry after one minute.") from exc
        fetched = datetime.now(timezone.utc).isoformat()
        # Bound observation-query cache memory in a long-running server.
        for key in list(_awc_cache):
            if _awc_cache[key][0] <= now:
                del _awc_cache[key]
        if len(_awc_cache) >= 128:
            del _awc_cache[next(key for key in _awc_cache if key != AWC_CATALOG)]
        _awc_cache[url] = (time.monotonic() + ttl, payload, fetched)
        return payload, fetched


def _metar_observations(stations, now):
    if not stations:
        return
    ids = sorted(row["id"] for row in stations)
    url = "https://aviationweather.gov/api/data/metar?" + urlencode({"ids": ",".join(ids), "format": "json"})
    try:
        reports, _ = _awc_resource(url, 60)
    except OSError:
        for station in stations:
            station["observation"] = {"status": "unavailable", "message": "Latest observations unavailable; station data link remains available."}
        return
    latest = {}
    for report in reports:
        instant = report.get("obsTime")
        if (report.get("icaoId") not in ids or isinstance(instant, bool)
                or not isinstance(instant, (int, float)) or not math.isfinite(instant)
                or not 0 <= instant <= now + 300):
            continue
        if instant > latest.get(report["icaoId"], {}).get("obsTime", -1):
            latest[report["icaoId"]] = report
    fields = [("temp", "Air temperature", "°C"), ("dewp", "Dew point", "°C"),
              ("wspd", "Wind speed", "kn"), ("wgst", "Wind gust", "kn"),
              ("wdir", "Wind direction", "°"), ("altim", "Altimeter pressure", "hPa")]
    for station in stations:
        report = latest.get(station["id"])
        if not report:
            station["observation"] = {"status": "missing", "message": "No recent report returned by NOAA."}
            continue
        values = []
        for key, label, unit in fields:
            value = report.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                values.append({"variable": label, "value": value, "unit": unit})
            elif key == "wdir" and value == "VRB":
                values.append({"variable": label, "value": "Variable", "unit": ""})
        # NOAA uses statute miles and may return a lower bound such as "10+".
        visibility = report.get("visib")
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?\+?", str(visibility)):
            values.append({"variable": "Visibility", "value": visibility, "unit": "statute mi"})
        age = max(0, now - report["obsTime"])
        station["observation"] = {
            "status": "missing" if not values else "stale" if age > 7200 else "available",
            "observed_at": datetime.fromtimestamp(report["obsTime"], timezone.utc).isoformat(),
            "age_minutes": round(age / 60, 1), "values": values,
            "message": "Fields absent from the report are not available. Reports older than 2 hours are stale; no values are filled in."}


def nearest_stations(lat, lon, n=5):
    """Nearest n unique METAR catalog stations within 200 km, independent of peril."""
    coordinates(lat, lon)
    n = station_count(n)
    rows, fetched = _awc_resource(AWC_CATALOG, 86400, compressed=True)
    if not rows:
        raise OSError("NOAA returned an empty station catalog")
    unique = {}
    skipped = 0
    for row in rows:
        if not isinstance(row.get("siteType"), list) or "METAR" not in row["siteType"]:
            continue
        identifier = row.get("icaoId")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Z0-9]{3,8}", identifier):
            skipped += 1
            continue
        try:
            if any(isinstance(row.get(key), bool) for key in ("lat", "lon")):
                raise ValueError("Invalid coordinates")
            other_lat, other_lon = float(row["lat"]), float(row["lon"])
            coordinates(other_lat, other_lon)
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue
        name = row.get("site") or identifier
        station = {"id": identifier, "name": str(name), "network": AWC_NETWORK,
                   "latitude": other_lat, "longitude": other_lon,
                   "distance_km": distance_km(lat, lon, other_lat, other_lon),
                   "measured_variables": METAR_VARIABLES,
                   "variables_note": "METAR report variables; availability varies by station and report.",
                   "url": "https://aviationweather.gov/data/metar/?" + urlencode({"id": identifier})}
        # Resolve repeated identifiers deterministically, independent of query distance.
        prior = unique.get(identifier)
        if prior is None or (other_lat, other_lon, station["name"]) < (prior["latitude"], prior["longitude"], prior["name"]):
            unique[identifier] = station
    if not unique:
        raise OSError("NOAA catalog contains no usable METAR stations")
    stations = sorted((row for row in unique.values() if row["distance_km"] <= RADIUS_KM),
                      key=lambda row: (row["distance_km"], row["id"]))[:n]
    now = time.time()
    _metar_observations(stations, now)
    for station in stations:
        station["observation"]["heat"] = heat_context(station["observation"], now)
    return {"status": "available" if stations else "not_found", "network": AWC_NETWORK,
            "network_id": "awc_metar", "requested_count": n, "returned_count": len(stations),
            "fewer_than_requested": len(stations) < n, "stations": stations,
            "search_radius_km": RADIUS_KM, "catalog_fetched_at": fetched,
            "skipped_catalog_rows": skipped,
            "coverage": "Worldwide NOAA AWC catalog entries marked METAR with valid coordinates, within 200 km of the selection. US street addresses via Census; coordinates worldwide.",
            "message": "Sorted by great-circle distance from the exact selection (area center for rectangles). METAR weather stations only, independent of the selected hazard. Catalog membership does not guarantee live telemetry. Station observations do not change hazard curves, probabilities or payouts."}


def _seismic_candidates(lat, lon, radius_km):
    bucket = int(time.time() // 600)
    instant = datetime.fromtimestamp(bucket * 600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    query = urlencode({"format": "text", "level": "station", "latitude": lat,
                       "longitude": lon, "maxradius": math.degrees((radius_km + 8) / EARTH_KM),
                       "starttime": instant, "endtime": instant, "channel": "?H?,?N?"})
    raw = _download(f"{SEISMIC_URL}?{query}", bucket)
    # FDSN returns HTTP 204 with an empty body when there are no stations.
    if not raw.strip():
        return
    if not raw.lstrip().startswith("#Network"):
        raise OSError("Unexpected seismic station catalog response")
    for fields in csv.reader(io.StringIO(raw), delimiter="|"):
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) < 8:
            raise OSError("Incomplete seismic station catalog response")
        network, station, lat, lon, _, name, start, end = (value.strip() for value in fields[:8])
        # Reject closed/future epochs even if a provider ignores the time filter.
        if start and start > instant or end and end <= instant:
            continue
        if not network or not station:
            continue
        link = urlencode({"network": network, "station": station, "level": "station", "format": "text"})
        yield {"id": f"{network}.{station}", "name": name or f"{network}.{station}",
               "latitude": lat, "longitude": lon, "url": f"{SEISMIC_URL}?{link}"}


def _raws_candidates(west, south, east, north):
    offset = 0
    while True:
        query = urlencode({"f": "json", "where": "Status = 'A'",
                           "geometry": f"{west},{south},{east},{north}",
                           "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                           "spatialRel": "esriSpatialRelIntersects", "returnGeometry": "false",
                           "outFields": "OBJECTID,StationName,WXID,Latitude,Longitude,Status,NOAA_URL",
                           "orderByFields": "OBJECTID", "resultRecordCount": 1000,
                           "resultOffset": offset})
        payload = json.loads(_download(f"{RAWS_URL}?{query}", int(time.time() // 600)))
        if "error" in payload or not isinstance(payload.get("features"), list):
            raise OSError("NIFC fire-weather station catalog is unavailable")
        for feature in payload["features"]:
            row = feature.get("attributes", {})
            if row.get("Status") != "A" or row.get("OBJECTID") is None:
                continue
            # Link directly to NOAA when provided, otherwise to this catalog record.
            link = row.get("NOAA_URL") or ""
            if not link.startswith("https://www.weather.gov/"):
                link = f"{RAWS_URL}?" + urlencode({"objectIds": row["OBJECTID"], "outFields": "*", "f": "pjson"})
            yield {"id": "RAWS-" + str(row.get("WXID") or row["OBJECTID"]),
                   "name": row.get("StationName") or "RAWS fire-weather station",
                   "latitude": row.get("Latitude"), "longitude": row.get("Longitude"), "url": link}
        if not payload.get("exceededTransferLimit"):
            break
        if not payload["features"]:
            raise OSError("Incomplete NIFC fire-weather station catalog response")
        offset += len(payload["features"])


def distance_km(lat, lon, other_lat, other_lon):
    a, b = map(math.radians, (lat, other_lat))
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(other_lon-lon)/2)**2
    return 2 * EARTH_KM * math.asin(math.sqrt(min(1, max(0, h))))


@lru_cache(maxsize=128)
def _download(url, time_bucket):
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp/0.1 monitoring-station lookup"})
    with urllib.request.urlopen(request, timeout=6) as response:
        return response.read().decode("utf-8")


def _candidates(peril, lat, lon, radius_km=RADIUS_KM):
    # Reuse downloads across nearby clicks. Points rounded to this 0.1-degree
    # center are within 8 km, so padding preserves complete search coverage.
    lat, lon = round(lat, 1), round(lon, 1)
    if peril == "earthquake":
        yield from _seismic_candidates(lat, lon, radius_km)
        return
    # Spherical bounding box encloses the complete search circle. Split at the dateline.
    angular = (radius_km + 8) / EARTH_KM
    delta = math.degrees(angular)
    south, north = max(-90, lat-delta), min(90, lat+delta)
    width = 180 if south == -90 or north == 90 else math.degrees(math.asin(min(1, math.sin(angular)/math.cos(math.radians(lat)))))
    west, east = lon-width, lon+width
    boxes = [(max(-180, west), south, min(180, east), north)]
    if west < -180:
        boxes.append((west+360, south, 180, north))
    if east > 180:
        boxes.append((-180, south, east-360, north))
    for west, south, east, north in boxes:
        # USGS accepts at most seven decimal places. Round outward to keep
        # stations on the radius boundary inside the queried box.
        west, south = (math.floor(value * 1e6) / 1e6 for value in (west, south))
        east, north = (math.ceil(value * 1e6) / 1e6 for value in (east, north))
        bbox = f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"
        if peril == "wildfire":
            yield from _raws_candidates(west, south, east, north)
        elif peril == "cyclone":
            query = urlencode({"format": "json", "bbox": f"{south},{west},{north},{east}"})
            payload = json.loads(_download(f"https://aviationweather.gov/api/data/stationinfo?{query}", int(time.time()//600)) or "[]")
            for row in payload:
                if "METAR" in row.get("siteType", []):
                    yield {"id": row["id"], "name": row["site"], "latitude": row["lat"], "longitude": row["lon"],
                           "url": "https://aviationweather.gov/data/metar/?" + urlencode({"id": row["id"]})}
        else:
            query = urlencode({"format": "rdb", "bBox": bbox,
                               "parameterCd": "00065" if peril == "flood" else "00045", "siteStatus": "active", "siteType": "ST" if peril == "flood" else "AT,ST"})
            raw = _download(f"https://waterservices.usgs.gov/nwis/site/?{query}", int(time.time()//600))
            lines = [line for line in raw.splitlines() if line and not line.startswith("#")]
            reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t")
            next(reader, None)  # RDB column-width/type row.
            for row in reader:
                yield {"id": row["site_no"], "name": row["station_nm"], "latitude": row["dec_lat_va"],
                       "longitude": row["dec_long_va"], "url": "https://waterdata.usgs.gov/monitoring-location/USGS-" + row["site_no"] + "/"}


def nearest_station(peril, lat, lon):
    coordinates(lat, lon)
    if peril == "fireline_intensity":
        result = nearest_station("wildfire", lat, lon)
        result["peril"] = peril
        result["message"] += " RAWS do not measure the modeled fireline intensity."
        return result
    if peril not in NETWORKS:
        raise ValueError("Unknown hazard")
    result = {"peril": peril, "station": None, "search_radius_km": RADIUS_KM}
    result["network"] = NETWORKS[peril]
    stations = []
    for radius in SEARCH_RADII_KM:
        for candidate in _candidates(peril, lat, lon, radius):
            row = dict(candidate)
            try:
                row["latitude"], row["longitude"] = float(row["latitude"]), float(row["longitude"])
                coordinates(row["latitude"], row["longitude"])
            except (ValueError, TypeError):
                continue
            row["distance_km"] = distance_km(lat, lon, row["latitude"], row["longitude"])
            if row["distance_km"] <= radius:
                stations.append(row)
        # The entire circle was searched, so anything outside it is farther.
        if stations:
            break
    result["status"] = "available" if stations else "not_found"
    result["station"] = min(stations, key=lambda row: (row["distance_km"], row["id"])) if stations else None
    result["message"] = "Nearest in this network within 200 km, by straight-line distance. Station context is separate from the hazard model; current readings and sensor availability are not checked."
    if peril == "flood":
        result["message"] += " A nearby stream gauge may be on a different watershed; it does not measure flood depth at your location."
    elif peril == "earthquake":
        result["message"] += " Seismic instruments monitor ground motion; a station's operating epoch does not guarantee live telemetry or predict an earthquake."
    elif peril == "wildfire":
        result["message"] += " RAWS monitor fire-weather conditions, not fire occurrence or flame length at your location. Catalog active status does not guarantee live readings."
    return result
