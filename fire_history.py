"""Nearest reported US wildfire across the full FPA FOD and WFIGS histories."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import copy
import json
import math
import threading
import time
import urllib.request
from urllib.parse import urlencode

from hazard_engine import coordinates
from monitoring_stations import distance_km

EARTH_KM = 6371.0088
REFRESH_SECONDS = 300
FPA_URL = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_FireOccurrenceCurrentEdition_01/MapServer/0"
WFIGS_URL = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations/FeatureServer/0"
SOURCES = (
    {"id": "fpa_fod", "name": "USFS FPA FOD — current edition", "url": FPA_URL,
     "where": "1=1", "oid": "objectid", "fields": "objectid,fod_id,fire_name,discovery_date,fire_size,fire_year",
     "coverage": "Published US wildfire archive; current edition covers 1992–2024. Updated when a new edition is published."},
    {"id": "wfigs", "name": "NIFC WFIGS / IRWIN — full incident history", "url": WFIGS_URL,
     "where": "IncidentTypeCategory='WF' AND IsValid=1 AND (IsQuarantined=0 OR IsQuarantined IS NULL)",
     "oid": "OBJECTID", "fields": "OBJECTID,IrwinID,IncidentName,FireDiscoveryDateTime,IncidentSize,ModifiedOnDateTime_dt",
     "coverage": "All available valid wildfire records in the full-history service; nominal coverage from 2014 onward, with some older records. Refreshed from IRWIN every 5 minutes."},
)
_cache = {}
_lock = threading.Lock()
SEARCH_PAGE_BUDGET = 5  # pages of 1000 per source before a radius is narrowed instead of downloaded


class TooManyRecords(OSError):
    """The search envelope holds more records than the page budget allows."""


RETRY_ATTEMPTS = 4
RETRY_DELAYS = (0.3, 0.8, 1.5)  # seconds before attempts 2, 3 and 4


def _json_once(url, params, remaining):
    request = urllib.request.Request(url + "?" + urlencode(params), headers={"User-Agent": "NDApp-fire-history/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=min(12, remaining)) as response:
        data = json.load(response)
    if not isinstance(data, dict) or "error" in data:
        raise OSError("Fire-history provider returned an error")
    return data


def _json(url, params, deadline):
    """ArcGIS JSON request, retried within the caller's deadline.

    The USFS FPA FOD service intermittently answers a valid request with an
    in-body {"error": {"code": 404, "message": "Layer not found"}} (observed
    2026-11-22, roughly half of requests), apparently from some servers behind
    its load balancer. Provider errors, network failures and malformed JSON are
    retried; a failure on every attempt still raises, so no data is invented.
    """
    last = None
    for attempt in range(RETRY_ATTEMPTS):
        if attempt:
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
            if deadline - time.monotonic() <= delay:
                break
            time.sleep(delay)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return _json_once(url, params, remaining)
        except (OSError, ValueError) as exc:  # URLError/HTTPError/timeouts are OSError; bad JSON is ValueError
            last = exc
    if last is None:
        raise OSError("Fire-history lookup timed out")
    raise OSError(f"Fire-history provider failed after retries: {last}") from last


def envelopes(lat, lon, radius):
    """Conservative spherical-circle bounds, split at the date line."""
    angle = min(math.pi, radius / EARTH_KM)
    south, north = max(-90, lat - math.degrees(angle)), min(90, lat + math.degrees(angle))
    if south <= -90 or north >= 90:
        return [(-180, south, 180, north)]
    delta = math.degrees(math.asin(min(1, math.sin(angle) / math.cos(math.radians(lat)))))
    west, east = lon - delta, lon + delta
    if west < -180:
        return [(-180, south, east, north), (west + 360, south, 180, north)]
    if east > 180:
        return [(west, south, 180, north), (-180, south, east - 360, north)]
    return [(west, south, east, north)]


def _query(source, bounds):
    return {"f": "json", "where": source["where"], "geometry": ",".join(str(v) for v in bounds),
            "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"}


def _search_bounds(lat, lon, radius):
    # A slight outward margin also protects bounds against datum/rounding effects.
    return envelopes(lat, lon, radius * 1.002 + 0.01)


def _count(source, lat, lon, radius, deadline):
    """Number of records in the search envelope, without downloading them."""
    total = 0
    for bounds in _search_bounds(lat, lon, radius):
        count = _json(source["url"] + "/query", {**_query(source, bounds), "returnCountOnly": "true"}, deadline).get("count")
        if not isinstance(count, int) or count < 0:
            raise OSError("Missing fire-history record count")
        total += count
    return total


def _records(source, lat, lon, radius, deadline, max_pages=100):
    records = []
    for bounds in _search_bounds(lat, lon, radius):
        offset = 0
        previous_page = None
        for _ in range(max_pages):
            params = {**_query(source, bounds), "outFields": source["fields"], "outSR": 4326,
                      "returnGeometry": "true", "orderByFields": source["oid"] + " ASC",
                      "resultOffset": offset, "resultRecordCount": 1000}
            page = _json(source["url"] + "/query", params, deadline)
            rows = page.get("features")
            if not isinstance(rows, list):
                raise OSError("Missing fire-history features")
            # Do not silently accept a truncated or non-paginating provider.
            fingerprint = tuple(row.get("attributes", {}).get(source["oid"]) for row in rows)
            if rows and fingerprint == previous_page:
                raise OSError("Fire-history provider repeated a page")
            records.extend(rows)
            if not page.get("exceededTransferLimit", False):
                break
            if not rows:
                raise OSError("Incomplete fire-history page")
            previous_page = fingerprint
            offset += len(rows)
        else:
            raise TooManyRecords("Fire-history search exceeded its page budget")
    return records


def _date(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _record(source, feature, lat, lon, now):
    attributes, geometry = feature.get("attributes", {}), feature.get("geometry", {})
    try:
        y, x = float(geometry["y"]), float(geometry["x"])
        coordinates(y, x)
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    fpa = source["id"] == "fpa_fod"
    discovered = _date(attributes.get("discovery_date" if fpa else "FireDiscoveryDateTime"))
    if discovered is None or discovered > now:
        return None
    size = attributes.get("fire_size" if fpa else "IncidentSize")
    try:
        size = float(size) if size is not None else None
        if size is not None and (not math.isfinite(size) or size < 0):
            size = None
    except (TypeError, ValueError, OverflowError):
        size = None
    oid = attributes[source["oid"]]
    return {"id": str(attributes.get("fod_id" if fpa else "IrwinID") or oid),
            "name": str(attributes.get("fire_name" if fpa else "IncidentName") or "Unnamed reported wildfire"),
            "latitude": y, "longitude": x, "distance_km": distance_km(lat, lon, y, x),
            "discovered_at": discovered, "discovery_date": discovered[:10],
            "date_note": "Reported discovery date; source time precision varies.",
            "reported_acres": size, "source_id": source["id"], "source_name": source["name"],
            "source_url": source["url"], "record_url": source["url"] + "/query?" + urlencode({
                "f": "pjson", "where": f"{source['oid']}={int(oid)}", "outFields": "*", "outSR": 4326}),
            "record_updated_at": _date(attributes.get("ModifiedOnDateTime_dt")) if not fpa else None}


def nearest_fire(lat, lon):
    coordinates(lat, lon)
    key = (lat, lon)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < REFRESH_SECONDS:
            return copy.deepcopy(cached[1])
    now = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + 45
    fire = None
    radius = None
    # Complete both sources at the same radius before declaring a nearest result.
    # Anything outside the searched spherical circle cannot beat an inside result.
    with ThreadPoolExecutor(max_workers=2) as pool:
        def candidates_within(r):
            futures = [pool.submit(_records, source, lat, lon, r, deadline, SEARCH_PAGE_BUDGET) for source in SOURCES]
            found = []
            for source, future in zip(SOURCES, futures):
                for feature in future.result():
                    record = _record(source, feature, lat, lon, now)
                    if record is not None and record["distance_km"] <= r + 1e-8:
                        found.append(record)
            return found

        def too_dense(r):
            counts = pool.map(lambda source: _count(source, lat, lon, r, deadline), SOURCES)
            return max(counts) > SEARCH_PAGE_BUDGET * 1000

        empty_within = 0.0  # radius already shown to hold no record inside its circle
        for step in (2, 10, 50, 250, 1250, 5000, 10000, math.pi * EARTH_KM):
            radius = step
            try:
                candidates = candidates_within(radius)
            except TooManyRecords:
                # Far from the nearest record a step can jump into a dense region. Narrow the radius
                # between the last empty circle and this one with cheap count queries instead.
                low, high, candidates = empty_within, radius, []
                while not candidates:
                    if high - low < 0.05:
                        if high == step:  # everything up to this step was empty; try the next step
                            radius = step
                            break
                        raise OSError("Fire-history records are too dense to search")
                    radius = (low + high) / 2
                    if too_dense(radius):
                        high = radius
                        continue
                    candidates = candidates_within(radius)
                    if not candidates:
                        low = radius
            if candidates:
                fire = min(candidates, key=lambda row: (row["distance_km"], row["source_id"], row["id"]))
                break
            empty_within = radius
    data = {"status": "available" if fire else "not_found", "time_window": "all_available_history",
            "fire": fire, "selection": {"latitude": lat, "longitude": lon},
            "searched_radius_km": radius, "checked_at": datetime.now(timezone.utc).isoformat(),
            "refresh_seconds": REFRESH_SECONDS,
            "sources": [{k: source[k] for k in ("id", "name", "url", "coverage")} for source in SOURCES],
            "message": "Nearest dated reported wildfire point across both source histories. Distance is straight-line great-circle distance to the reported point, not the fire perimeter. Coverage is US reporting networks and is incomplete; records may be approximate or revised. A nearby incident does not establish that the selected property burned."}
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), copy.deepcopy(data))
    return data
