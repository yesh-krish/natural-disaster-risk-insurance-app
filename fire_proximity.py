"""Historical annual frequency of at least one reported wildfire within a radius."""
import copy
from datetime import datetime, timezone
import math
import threading
import time

from fire_history import FPA_URL, REFRESH_SECONDS, SOURCES, _date, _json, _record, _records
from hazard_engine import coordinates
from hazard_relevance import NRI_LAYER

RADII_KM = (5, 10, 20)
_cache = {}
_lock = threading.Lock()
COVERAGE_ASSUMPTION = (
    "All calendar years in the published baseline are included. A year with no eligible "
    "archive record in a radius is counted as zero reported-fire years for that radius. "
    "Local reporting completeness has not been independently verified."
)
NOTES = [
    "Historical annual frequency of reported incident points, not a calibrated future probability or property burn probability.",
    "Only the published USFS FPA FOD archive is used for frequencies; live WFIGS records and unfinished recent years are excluded.",
    "Each year counts at most once per radius, even when many fires are reported. Radii are nested and their frequencies must not be added.",
    "Points can be approximate to a one-square-mile PLSS section; distance is to the reported point, not the fire perimeter.",
    "Reporting omissions, changes in reporting, fuels and climate are not corrected. US records only; cross-border fires may be omitted.",
    "A zero observed frequency does not establish zero future risk. Reference intervals assume independent, comparable years and omit reporting and model uncertainty.",
]


def wilson_interval(successes, years):
    """95% binomial reference interval; not a forecast or a coverage correction."""
    z = 1.959963984540054
    proportion = successes / years
    denominator = 1 + z * z / years
    middle = (proportion + z * z / (2 * years)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / years + z * z / (4 * years * years)) / denominator
    return {"minimum_pct": max(0, middle - half) * 100,
            "maximum_pct": min(1, middle + half) * 100, "method": "wilson_95_binomial_reference"}


def summarize(records, start_year, end_year):
    """Keep zero-record years in the denominator; retain the nearest in-range point."""
    annual = {year: [] for year in range(start_year, end_year + 1)}
    for record in records:
        year = int(record["discovery_date"][:4])
        if year in annual and record["distance_km"] <= max(RADII_KM) + 1e-8:
            annual[year].append(record)
    rows = []
    for year, fires in annual.items():
        closest = min(fires, key=lambda fire: (fire["distance_km"], fire["id"])) if fires else None
        rows.append({"year": year, "reported_fire_count_within_20_km": len(fires),
                     "nearest_distance_within_20_km": closest["distance_km"] if closest else None,
                     "nearest_fire": closest,
                     "within_radii": {str(radius): any(fire["distance_km"] <= radius + 1e-8 for fire in fires)
                                      for radius in RADII_KM}})
    summaries = []
    for radius in RADII_KM:
        years_with_fire = [row["year"] for row in rows if row["within_radii"][str(radius)]]
        summaries.append({"radius_km": radius, "years_with_fire": len(years_with_fire),
                          "total_years": len(rows), "frequency_pct": len(years_with_fire) / len(rows) * 100,
                          "qualifying_years": years_with_fire,
                          "reference_interval": wilson_interval(len(years_with_fire), len(rows))})
    return summaries, rows


def _baseline(metadata, now):
    extent = metadata.get("timeInfo", {}).get("timeExtent", [])
    if len(extent) != 2:
        raise OSError("Published archive time coverage is missing")
    dates = [_date(value) for value in extent]
    if not all(dates) or dates[0] > dates[1]:
        raise OSError("Published archive time coverage is invalid")
    start = datetime.fromisoformat(dates[0])
    end = datetime.fromisoformat(dates[1])
    # Exclude partially covered edge years and the in-progress calendar year.
    first = start.year + int((start.month, start.day) != (1, 1))
    last = min(end.year - int((end.month, end.day) != (12, 31)), now.year - 1)
    if not 1900 <= first <= last or last - first + 1 < 20:
        raise OSError("Fewer than 20 full published calendar years are available")
    return {"start_year": first, "end_year": last, "year_count": last - first + 1,
            "source_time_extent": dates, "source_layer": metadata["name"],
            "local_reporting_completeness_verified": False, "coverage_assumption": COVERAGE_ASSUMPTION}


def fire_proximity(lat, lon):
    coordinates(lat, lon)
    key = (lat, lon)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < REFRESH_SECONDS:
            return copy.deepcopy(cached[1])
    deadline = time.monotonic() + 45
    now = datetime.now(timezone.utc)
    county_data = _json(NRI_LAYER + "/query", {
        "f": "json", "where": "1=1", "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "COUNTY,STATE,STCOFIPS",
        "returnGeometry": "false"}, deadline)
    counties = county_data.get("features")
    if not isinstance(counties, list) or county_data.get("exceededTransferLimit"):
        raise OSError("US geographic coverage could not be checked")
    result = {"status": "unavailable", "frequency_basis": "historical_annual_reported_fire_frequency",
              "selection": {"latitude": lat, "longitude": lon}, "baseline": None,
              "radii_km": list(RADII_KM), "frequencies": [], "annual_records": [],
              "source_name": SOURCES[0]["name"], "source_url": FPA_URL,
              "coverage_check_url": NRI_LAYER, "refresh_seconds": REFRESH_SECONDS, "notes": NOTES.copy()}
    if not counties:
        result["reason"] = "Annual frequencies are unavailable outside mapped US county coverage. No zero frequency was substituted."
    else:
        county = sorted((row["attributes"] for row in counties), key=lambda attrs: str(attrs["STCOFIPS"]))[0]
        if not county.get("COUNTY") or not county.get("STATE") or not county.get("STCOFIPS"):
            raise OSError("US geographic coverage metadata is incomplete")
        result["county"] = {"name": county["COUNTY"], "state": county["STATE"], "fips": county["STCOFIPS"]}
        metadata = _json(FPA_URL, {"f": "json"}, deadline)
        baseline = _baseline(metadata, now)
        features = _records(SOURCES[0], lat, lon, max(RADII_KM), deadline)
        records = {}
        for feature in features:
            record = _record(SOURCES[0], feature, lat, lon, now.isoformat())
            # Invalid returned data must not silently become a zero year.
            if record is None:
                raise OSError("Archive contains an invalid or undated record in the search area")
            identity = record["id"]
            if identity in records and records[identity] != record:
                raise OSError("Archive changed during pagination; retry the lookup")
            records[identity] = record
        frequencies, rows = summarize(records.values(), baseline["start_year"], baseline["end_year"])
        result.update(status="available", baseline=baseline, frequencies=frequencies, annual_records=rows,
                      reason="Years with at least one reported fire in the radius divided by all published baseline years.")
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), copy.deepcopy(result))
    return result
