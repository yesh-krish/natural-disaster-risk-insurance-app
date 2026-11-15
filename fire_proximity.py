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
# FPA FOD size classes: C ≥10, D ≥100, F ≥1,000, G ≥5,000 acres.
SIZE_THRESHOLDS_ACRES = (10, 100, 1000, 5000)
# Burned-area share is only computed where fires reported beyond the 20 km query
# would need to be very large to overlap the circle.
AREA_RADII_KM = (5, 10)
KM2_PER_ACRE = 0.00404685642
# Burned-area thresholds for the exceedance curve (acres), 1–3 steps per decade.
CURVE_ACRES = (0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000)
SIZE_NOTES = [
    "Size thresholds use the final reported fire size in acres. Each year counts at most once per radius and threshold.",
    "Footprints are equivalent circles of the reported area centered on the reported point. Real perimeters are irregular, "
    "the point can be the ignition location rather than the center, and points can be off by up to about 1.6 km.",
    "Burned-area share is reported acres overlapping the circle divided by circle area and baseline years. It is an "
    "average for the whole circle, not this property's burn probability. Reburned land is counted again.",
    "The burned-area exceedance curve uses each fire's reported size and the historical rate of such fires. "
    "Annual probability assumes fires occur independently at the 1992–2024 rate (Poisson). It is not extrapolated "
    "beyond the largest reported fire, and it is not the chance that this property burns.",
    "Only fires reported within 20 km are retrieved. Larger fires reported farther away are omitted; each result states "
    "the smallest omitted fire size that could have affected it.",
]
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


def footprint_radius_km(acres):
    """Radius of a circle with the reported burned area."""
    return math.sqrt(acres * KM2_PER_ACRE / math.pi)


def circle_overlap_km2(r1, r2, d):
    """Intersection area of two circles with radii r1, r2 whose centers are d apart."""
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return math.pi * min(r1, r2) ** 2
    a1 = r1 * r1 * math.acos(max(-1.0, min(1.0, (d * d + r1 * r1 - r2 * r2) / (2 * d * r1))))
    a2 = r2 * r2 * math.acos(max(-1.0, min(1.0, (d * d + r2 * r2 - r1 * r1) / (2 * d * r2))))
    kite = 0.5 * math.sqrt(max(0.0, (-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2)))
    return a1 + a2 - kite


def area_exceedance_curve(sized, years):
    """Annual exceedance of reported burned area per fire, for each radius.

    expected_annual_exceedances = fires of at least A acres per baseline year (Poisson rate).
    annual_probability_pct = 1 - exp(-rate): chance of at least one such fire in a year if the
    historical rate holds. empirical_years_pct counts years that actually had one.
    The curve stops at the first threshold above the largest reported fire; nothing is extrapolated.
    """
    curves = []
    for radius in RADII_KM:
        fires = [fire for fire in sized if fire["distance_km"] <= radius + 1e-8 and fire["reported_acres"] > 0]
        largest = max((fire["reported_acres"] for fire in fires), default=0)
        points = []
        for acres in CURVE_ACRES:
            hits = [fire for fire in fires if fire["reported_acres"] >= acres]
            rate = len(hits) / years
            probability = 1 - math.exp(-rate)
            hit_years = {int(fire["discovery_date"][:4]) for fire in hits}
            points.append({"min_acres": acres, "fires_at_or_above": len(hits),
                           "expected_annual_exceedances": rate, "annual_probability_pct": probability * 100,
                           "empirical_years_pct": len(hit_years) / years * 100,
                           "return_period_years": 1 / probability if probability > 0 else None})
            if not hits:
                break
        curves.append({"radius_km": radius, "fire_count": len(fires), "total_years": years,
                       "largest_reported_acres": largest or None,
                       "expected_annual_reported_acres": sum(fire["reported_acres"] for fire in fires) / years,
                       "points": points})
    return {"method": "poisson_rate_of_reported_fire_size", "thresholds_acres": list(CURVE_ACRES),
            "curves": curves}


def _omitted_acres(gap_km):
    """Smallest fire, reported beyond the query, whose equivalent circle could span gap_km."""
    return math.pi * gap_km * gap_km / KM2_PER_ACRE


def size_exceedance(records, start_year, end_year):
    """Combine reported fire size with distance: size thresholds, footprint reach and burned-area share."""
    query_km = max(RADII_KM)
    years = end_year - start_year + 1
    fires = [fire for fire in records if start_year <= int(fire["discovery_date"][:4]) <= end_year
             and fire["distance_km"] <= query_km + 1e-8]
    sized = [fire for fire in fires if fire.get("reported_acres") is not None]
    thresholds = []
    for acres in SIZE_THRESHOLDS_ACRES:
        for radius in RADII_KM:
            hits = sorted({int(fire["discovery_date"][:4]) for fire in sized
                           if fire["reported_acres"] >= acres and fire["distance_km"] <= radius + 1e-8})
            thresholds.append({"min_acres": acres, "radius_km": radius, "years_with_fire": len(hits),
                               "total_years": years, "frequency_pct": len(hits) / years * 100,
                               "qualifying_years": hits, "reference_interval": wilson_interval(len(hits), years)})
    reaching = sorted((fire for fire in sized if fire["reported_acres"] > 0
                       and fire["distance_km"] <= footprint_radius_km(fire["reported_acres"])),
                      key=lambda fire: (fire["discovery_date"], fire["id"]))
    reach_years = sorted({int(fire["discovery_date"][:4]) for fire in reaching})
    closest_edge = min(((fire["distance_km"] - footprint_radius_km(fire["reported_acres"]), fire) for fire in sized
                        if fire["reported_acres"] > 0), key=lambda pair: (pair[0], pair[1]["id"]), default=(None, None))
    reach = {"years_with_reach": len(reach_years), "total_years": years, "frequency_pct": len(reach_years) / years * 100,
             "qualifying_years": reach_years, "reference_interval": wilson_interval(len(reach_years), years),
             "fires": [{"id": fire["id"], "name": fire["name"], "discovery_date": fire["discovery_date"],
                        "reported_acres": fire["reported_acres"], "distance_km": fire["distance_km"],
                        "footprint_radius_km": footprint_radius_km(fire["reported_acres"]),
                        "record_url": fire["record_url"]} for fire in reaching],
             "closest_footprint_gap_km": None if closest_edge[1] is None else max(0.0, closest_edge[0]),
             "closest_footprint_fire_id": None if closest_edge[1] is None else closest_edge[1]["id"],
             "omits_fires_reported_beyond_km": query_km, "omitted_fire_min_acres": _omitted_acres(query_km)}
    burned = []
    for radius in AREA_RADII_KM:
        circle = math.pi * radius * radius
        overlap = sum(circle_overlap_km2(radius, footprint_radius_km(fire["reported_acres"]), fire["distance_km"])
                      for fire in sized if fire["reported_acres"] > 0)
        share = overlap / circle / years
        burned.append({"radius_km": radius, "circle_area_km2": circle, "overlapping_reported_area_km2": overlap,
                       "overlapping_reported_acres": overlap / KM2_PER_ACRE, "total_years": years,
                       "mean_annual_burned_share_pct": share * 100,
                       "equivalent_return_interval_years": 1 / share if share > 0 else None,
                       "omitted_fire_min_acres": _omitted_acres(query_km - radius)})
    return {"basis": "reported_size_and_distance", "size_thresholds_acres": list(SIZE_THRESHOLDS_ACRES),
            "records_in_query": len(fires), "records_missing_size": len(fires) - len(sized),
            "threshold_frequencies": thresholds, "area_exceedance": area_exceedance_curve(sized, years),
            "footprint_reach": reach, "burned_area": burned,
            "notes": SIZE_NOTES.copy()}


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
              "size_exceedance": None, "coverage_check_url": NRI_LAYER, "refresh_seconds": REFRESH_SECONDS, "notes": NOTES.copy()}
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
                      size_exceedance=size_exceedance(records.values(), baseline["start_year"], baseline["end_year"]),
                      reason="Years with at least one reported fire in the radius divided by all published baseline years.")
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), copy.deepcopy(result))
    return result
