"""Historical annual maximum daily air-temperature exceedance (station proxy).

Uses NOAA GHCN-Daily TMAX, never current METAR, Heat Index or WBGT.
See docs/heat-and-wind.md for baseline, coverage policy and interpretation.
"""
import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import io
import re
import urllib.request

from hazard_engine import ModelUnavailable, coordinates

ROOT_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
README_URL = ROOT_URL + "readme.txt"
START_YEAR, END_YEAR = 1991, 2020
MIN_COMPLETE_YEARS = 20  # Product coverage policy, not a NOAA safety threshold.
RADIUS_KM = 100
MAX_CANDIDATES = 5
# Per-station comparison table: each station is a separate NOAA download, so the
# table is capped even though the viewer's station count accepts up to 50.
MAX_COMPARISON_STATIONS = 10
NO_STATION_REASON = "No US GHCN-Daily TMAX station with inventory spanning 1991–2020 is within 100 km. Historical heat coverage is unavailable."


def _resource(name, limit):
    """Bounded download with request provenance; only fixed NOAA resource paths."""
    url = ROOT_URL + name
    request = urllib.request.Request(url, headers={"User-Agent": "NDApp/0.3 historical-heat research"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise OSError("NOAA historical heat response exceeds size limit")
    return raw.decode("ascii"), {"url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "fetched_at": datetime.now(timezone.utc).isoformat()}


INDEX_ELEMENTS = {"TMAX", "PRCP", "WSF2"}


@lru_cache(maxsize=1)
def ghcn_index(day):
    """US station metadata plus inventory year spans for INDEX_ELEMENTS.

    One daily download of the NOAA catalog, shared by the heat curve and the
    per-station rainfall/wind tables. Inventory span does not imply continuity.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        inventory_job = pool.submit(_resource, "ghcnd-inventory.txt", 64_000_000)
        metadata_job = pool.submit(_resource, "ghcnd-stations.txt", 16_000_000)
        inventory, inventory_provenance = inventory_job.result()
        metadata, metadata_provenance = metadata_job.result()
    stations = {}
    for line in io.StringIO(metadata):
        if line.startswith("US") and len(line.rstrip("\r\n")) >= 71:
            identifier = line[:11]
            if not re.fullmatch(r"US[A-Z0-9]{9}", identifier):
                continue
            try:
                lat, lon = float(line[12:20]), float(line[21:30])
                coordinates(lat, lon)
            except ValueError:
                continue
            stations[identifier] = {"id": identifier, "name": line[41:71].strip(),
                                    "latitude": lat, "longitude": lon,
                                    "url": ROOT_URL + "all/" + identifier + ".dly"}
    spans = {}
    for line in io.StringIO(inventory):
        element = line[31:35]
        if line[:11] not in stations or element not in INDEX_ELEMENTS:
            continue
        try:
            spans[(line[:11], element)] = (int(line[36:40]), int(line[41:45]))
        except ValueError:
            continue
    return stations, spans, [inventory_provenance, metadata_provenance]


@lru_cache(maxsize=1)
def station_catalog(day):
    """US TMAX stations whose inventory spans the fixed baseline; not a QC claim."""
    stations, spans, provenance = ghcn_index(day)
    eligible = {identifier for (identifier, element), (first, last) in spans.items()
                if element == "TMAX" and first <= START_YEAR and last >= END_YEAR}
    if not eligible:
        raise OSError("NOAA historical heat station inventory is empty or incompatible")
    return [stations[key] for key in sorted(eligible)], provenance


def annual_maxima(raw, station_id):
    """Only complete calendar years with unflagged daily TMAX enter the sample.

    Keep source days as dates, not invented UTC instants. Duplicate months fail
    closed rather than choosing a potentially different revision implicitly.
    """
    days = {year: {} for year in range(START_YEAR, END_YEAR + 1)}
    months = set()
    for line in io.StringIO(raw):
        if len(line) < 21 or line[17:21] != "TMAX":
            continue
        if line[:11] != station_id:
            raise ValueError("NOAA daily record station identity does not match request")
        year, month = int(line[11:15]), int(line[15:17])
        if not START_YEAR <= year <= END_YEAR:
            continue
        if len(line.rstrip("\r\n")) != 269 or not 1 <= month <= 12 or (year, month) in months:
            raise ValueError("Incomplete or duplicate NOAA TMAX month")
        months.add((year, month))
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            offset = 21 + (day - 1) * 8
            value = int(line[offset:offset+5])
            measurement_flag = line[offset+5]
            quality_flag = line[offset+6]
            source_flag = line[offset+7]
            if value == -9999 or quality_flag != " ":
                continue
            # All accepted inputs retain flags, including hourly-maximum H and
            # observation-time L flags. No de-biasing or imputation is applied.
            day_key = date(year, month, day).isoformat()
            days[year][day_key] = {
                "date": day_key, "temperature_c": value / 10,
                "measurement_flag": measurement_flag.strip(), "source_flag": source_flag.strip()}
    years = []
    for year, records in days.items():
        expected = 366 if calendar.isleap(year) else 365
        complete = len(records) == expected
        maximum = max(records.values(), key=lambda row: (row["temperature_c"], row["date"])) if complete else None
        years.append({"year": year, "expected_days": expected, "valid_days": len(records),
                      "excluded_or_missing_days": expected - len(records), "complete": complete,
                      "maximum_c": maximum["temperature_c"] if maximum else None,
                      "maximum_date": maximum["date"] if maximum else None,
                      "maximum_measurement_flag": maximum["measurement_flag"] if maximum else None,
                      "maximum_source_flag": maximum["source_flag"] if maximum else None})
    return years


@lru_cache(maxsize=16)
def station_text(identifier, day):
    """Raw GHCN-Daily station file (all elements); shared by heat, rainfall and wind."""
    if not re.fullmatch(r"US[A-Z0-9]{9}", identifier):
        raise ValueError("Invalid GHCN station identifier")
    return _resource("all/" + identifier + ".dly", 16_000_000)


@lru_cache(maxsize=16)
def station_years(identifier, day):
    raw, provenance = station_text(identifier, day)
    return annual_maxima(raw, identifier), provenance


def _nearby(lat, lon, day):
    """Baseline-spanning stations within RADIUS_KM, nearest first (ID breaks ties)."""
    coordinates(lat, lon)
    from monitoring_stations import distance_km
    stations, catalog_provenance = station_catalog(day)
    nearby = sorted(({**row, "distance_km": distance_km(lat, lon, row["latitude"], row["longitude"])} for row in stations),
                    key=lambda row: (row["distance_km"], row["id"]))
    return [row for row in nearby if row["distance_km"] <= RADIUS_KM], catalog_provenance


def station_comparison(lat, lon, requested):
    """Each nearby station's own empirical annual-maximum distribution.

    Stations are listed side by side and never averaged; the main heat curve
    still comes from heat_at's single qualifying station.
    """
    if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= 50:
        raise ValueError("Station count must be an integer from 1 to 50.")
    day = int(datetime.now(timezone.utc).timestamp() // 86400)
    within, catalog_provenance = _nearby(lat, lon, day)
    shown = within[:min(requested, MAX_COMPARISON_STATIONS)]

    def fetch(station):
        try:
            return station_years(station["id"], day)
        except (OSError, ValueError) as exc:
            return exc
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(fetch, shown))
    rows = []
    for station, outcome in zip(shown, fetched):
        row = {key: station[key] for key in ("id", "name", "latitude", "longitude", "distance_km", "url")}
        if isinstance(outcome, Exception):
            # A download/parse failure is reported as such, never as missing coverage.
            row.update(status="source_unavailable", complete_years=None, annual_maxima_c=[], points=[],
                       reason=f"NOAA daily record could not be read ({type(outcome).__name__}).")
            rows.append(row)
            continue
        years, provenance = outcome
        maxima = [year["maximum_c"] for year in years if year["complete"]]
        row.update(complete_years=len(maxima), source=provenance,
                   annual_maxima_c=maxima, highest_annual_maximum_c=max(maxima) if maxima else None)
        if len(maxima) < MIN_COMPLETE_YEARS:
            row.update(status="insufficient_years", points=[],
                       reason=f"{len(maxima)} complete {'year' if len(maxima) == 1 else 'years'}; at least {MIN_COMPLETE_YEARS} are required.")
        else:
            row.update(status="available", points=[
                {"severity": threshold, "frequency_pct": 100 * sum(value >= threshold for value in maxima) / len(maxima),
                 "hit_years": sum(value >= threshold for value in maxima), "exposure_years": len(maxima)}
                for threshold in sorted(set(maxima))])
        rows.append(row)
    return {"peril": "heat", "latitude": lat, "longitude": lon,
            "requested_stations": requested, "station_limit": MAX_COMPARISON_STATIONS,
            "stations_within_radius": len(within), "radius_km": RADIUS_KM,
            "baseline": {"start_year": START_YEAR, "end_year": END_YEAR, "minimum_complete_years": MIN_COMPLETE_YEARS},
            "frequency": "100 × complete years with annual maximum ≥ threshold / included complete years, per station",
            "stations": rows, "provenance": {"sources": catalog_provenance},
            "notes": ["Each row is one station's own 1991–2020 record. Stations are not averaged or combined.",
                      f"At most {MAX_COMPARISON_STATIONS} stations are listed because each requires a separate NOAA download.",
                      "Elevation, urban and siting differences between stations can explain differing frequencies."]}


def heat_at(lat, lon):
    day = int(datetime.now(timezone.utc).timestamp() // 86400)
    within, catalog_provenance = _nearby(lat, lon, day)
    candidates = within[:MAX_CANDIDATES]
    if not candidates:
        raise ModelUnavailable(NO_STATION_REASON)
    checked = []
    for station in candidates:
        years, daily_provenance = station_years(station["id"], day)
        complete = [row for row in years if row["complete"]]
        checked.append({"id": station["id"], "name": station["name"], "distance_km": station["distance_km"],
                        "complete_years": len(complete), "eligible": len(complete) >= MIN_COMPLETE_YEARS,
                        "source": daily_provenance})
        if len(complete) >= MIN_COMPLETE_YEARS:
            break
    distance = station["distance_km"]
    result = {"peril": "heat", "status": "available", "points": [],
              "metric": "Annual maximum daily air temperature", "unit": "°C",
              "source_name": "NOAA NCEI GHCN-Daily TMAX (station proxy)", "source_url": station["url"],
              "frequency_label": "Observed annual exceedance frequency, 1991–2020 (%)",
              "frequency_basis": "historical_annual_frequency",
              "station": station,
              "station_selection": {"max_candidates": MAX_CANDIDATES, "radius_km": RADIUS_KM, "checked": checked},
              "baseline": {"start_year": START_YEAR, "end_year": END_YEAR,
                           "complete_years": len(complete), "minimum_complete_years": MIN_COMPLETE_YEARS,
                           "excluded_years": [row["year"] for row in years if not row["complete"]]},
              "annual_maxima": years, "provenance": {"sources": [*catalog_provenance, daily_provenance]},
              "details": [["Historical station", f"{station['name']} ({station['id']})"],
                          ["Station distance", f"{distance:.2f} km; values describe this station, not a measurement at the selected point"],
                          ["Station coordinates", f"{station['latitude']:.5f}, {station['longitude']:.5f}"],
                          ["Baseline", "1991–2020 calendar years"],
                          ["Usable years", f"{len(complete)} of 30; 365/366 valid, unflagged daily TMAX readings required per included year"],
                          ["Frequency", "100 × complete years with annual maximum ≥ threshold / included complete years"],
                          ["Daily metric", "Maximum air temperature over the source observing day, not Heat Index or WBGT"]],
              "notes": ["Historical station-proxy frequencies, not calibrated future probabilities. The 1991–2020 baseline does not adjust for subsequent warming, station moves or observing-practice changes.",
                        "One day's maximum is sufficient to meet this trigger. It does not define a multi-day heat wave, overnight relief or a construction exposure limit.",
                        "Nearest qualifying station among the first five US TMAX stations whose inventories span the baseline within 100 km. All checked stations and exclusions are recorded. Local elevation, urban and site conditions can differ.",
                        "Missing/quality-flagged days exclude the entire year. At least 20 complete years are required by this app's coverage policy. Frequencies describe included years only; exclusions can bias the sample.",
                        "Only observed annual-maximum thresholds are supplied. No extrapolated rare-event tails or return periods. The curve is an empirical step distribution; connecting lines are visual guides."],
              "source_references": [{"title": "NOAA GHCN-Daily format, units and quality flags", "url": README_URL},
                                    {"title": "NOAA 1991–2020 reference-period context", "url": "https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals"}]}
    if len(complete) < MIN_COMPLETE_YEARS:
        result.update(status="unavailable", reason=f"None of {len(checked)} checked nearby baseline-spanning stations has at least {MIN_COMPLETE_YEARS} complete years. Missing data is not zero heat hazard.")
        return result
    maxima = [row["maximum_c"] for row in complete]
    if min(maxima) < 0:
        result.update(status="unavailable", reason="Negative annual temperature maxima are outside this viewer's supported severity domain; no values were clipped.")
        return result
    result["points"] = [{"severity": threshold,
                         "frequency_pct": 100 * sum(value >= threshold for value in maxima) / len(maxima),
                         "hit_years": sum(value >= threshold for value in maxima), "exposure_years": len(maxima)}
                        for threshold in sorted(set(maxima))]
    return result
