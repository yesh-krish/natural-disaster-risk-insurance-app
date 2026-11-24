"""Per-station historical annual-maximum tables for rainfall, wind and river flood.

Each row is one monitor's own record. Rows are listed side by side and never
averaged, and they do not replace the map's model curves (NOAA Atlas 14 rainfall,
HURDAT2 tropical-cyclone footprints, TWDB flood depths). See docs/station-records.md.

Sources:
- rainfall: NOAA GHCN-Daily PRCP (daily precipitation, tenths of mm), 1991–2020
- wind: NOAA GHCN-Daily WSF2 (fastest 2-minute wind, tenths of m/s) on tropical-storm days, 2004–2025
- flood: NOAA NWPS gauges with official flood stages; yearly highest river level from
  the linked USGS annual peak record, each gauge's systematic record
"""
import calendar
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import io
import json
import math
import re
import time
import urllib.request
from urllib.parse import urlencode

from hazard_engine import coordinates
import historical_heat as ghcn
from monitoring_stations import distance_km

RADIUS_KM = 100
MAX_STATIONS = 10  # Each station is a separate download.
MIN_YEARS = 20     # App coverage policy, matching historical heat.
MIN_DAY_FRACTION = 0.95  # GHCN year counts when ≥95% of its days are valid and unflagged.

KINDS = {
    "rainfall": {"element": "PRCP", "start": 1991, "end": 2020, "unit": "mm",
                 "metric": "Wettest calendar day of the year (daily precipitation)",
                 "source_name": "NOAA NCEI GHCN-Daily PRCP"},
    # Wind matches the main hurricane curve: the station's fastest 2-minute (sustained)
    # wind, counted only on days a tropical cyclone's recorded 34 kn wind area (HURDAT2,
    # same rule as the main curve) covered the station, 2004 onward like the main curve.
    # 60 m/s guard: early ASOS wind records repeat an implausible 65.7 m/s value.
    "wind": {"element": "WSF2", "start": 2004, "end": 2025, "unit": "m/s", "max_valid": 60, "storm_days": True,
             "metric": "Fastest 2-minute sustained wind on tropical-storm days, per year",
             "source_name": "NOAA NCEI GHCN-Daily WSF2 matched to NOAA NHC HURDAT2 storm wind areas"},
    # Flood: NWS river gauges with official flood stages; each water year's highest river
    # level from the linked USGS peak record, compared with that gauge's own flood stages.
    "flood": {"unit": "ft", "metric": "Highest river level of each water year at NWS flood-stage gauges",
              "source_name": "NOAA NWPS flood stages with USGS annual peak gage heights"},
}
NWPS_URL = "https://api.water.noaa.gov/nwps/v1/gauges"
FLOOD_LEVELS = ("action", "minor", "moderate", "major")
DATUM_TOLERANCE_FT = 1.0
USGS_PEAK_URL = "https://nwis.waterdata.usgs.gov/nwis/peak"


def _requested(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("Station count must be an integer from 1 to 50.")
    return value


def _points(maxima):
    return [{"severity": threshold, "frequency_pct": 100 * sum(v >= threshold for v in maxima) / len(maxima),
             "hit_years": sum(v >= threshold for v in maxima), "exposure_years": len(maxima)}
            for threshold in sorted(set(maxima))]


def ghcn_annual_maxima(raw, station_id, element, start, end, max_valid=None, only_days=None):
    """Annual maxima of one GHCN-Daily element; a year counts with ≥95% valid days.

    Missing or quality-flagged days are skipped, never imputed. Because a skipped
    day can only hide a larger value, counted-year maxima can be low. Values above
    max_valid (a sanity guard) are skipped like flagged days and tallied.

    only_days (a set of ISO dates) restricts the maximum to those days, e.g.
    tropical-storm days. A year with none of them has maximum 0. A year in which any
    of them lacks a reading is not counted: the storm maximum there is unknown.
    """
    days = {year: {} for year in range(start, end + 1)}
    months = set()
    rejected = 0
    for line in io.StringIO(raw):
        if len(line) < 21 or line[17:21] != element:
            continue
        if line[:11] != station_id:
            raise ValueError("NOAA daily record station identity does not match request")
        year, month = int(line[11:15]), int(line[15:17])
        if not start <= year <= end:
            continue
        if len(line.rstrip("\r\n")) != 269 or not 1 <= month <= 12 or (year, month) in months:
            raise ValueError(f"Incomplete or duplicate NOAA {element} month")
        months.add((year, month))
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            offset = 21 + (day - 1) * 8
            value = int(line[offset:offset+5])
            if value == -9999 or line[offset+6] != " " or value < 0:
                continue
            if max_valid is not None and value / 10 > max_valid:
                rejected += 1
                continue
            days[year][f"{year:04d}-{month:02d}-{day:02d}"] = value / 10
    years = []
    for year, records in days.items():
        expected = 366 if calendar.isleap(year) else 365
        counted = len(records) >= math.ceil(MIN_DAY_FRACTION * expected)
        entry = {"year": year, "valid_days": len(records), "expected_days": expected}
        if only_days is not None:
            storm = {day for day in only_days if day.startswith(f"{year:04d}-")}
            missing = sorted(storm - records.keys())
            counted = counted and not missing
            pool = {day: value for day, value in records.items() if day in storm}
            entry.update(storm_days=len(storm), storm_days_missing=missing)
        else:
            pool = records
        best = max(pool.items(), key=lambda item: (item[1], item[0])) if counted and pool else None
        entry.update(counted=counted, maximum=(best[1] if best else (0.0 if counted else None)),
                     maximum_date=best[0] if best else None)
        years.append(entry)
    return years, rejected


def _row(station, keys=("id", "name", "latitude", "longitude", "distance_km", "url")):
    return {key: station[key] for key in keys}


def _finish(row, maxima):
    row.update(counted_years=len(maxima), annual_maxima=maxima,
               highest=max(maxima) if maxima else None, median=_median(maxima))
    if len(maxima) < MIN_YEARS:
        row.update(status="insufficient_years", points=[],
                   reason=f"{len(maxima)} counted {'year' if len(maxima) == 1 else 'years'}; at least {MIN_YEARS} are required.")
    else:
        row.update(status="available", points=_points(maxima))
    return row


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle-1] + ordered[middle]) / 2


def _ghcn_rows(kind, lat, lon, shown_limit):
    spec = KINDS[kind]
    day = int(datetime.now(timezone.utc).timestamp() // 86400)
    stations, spans, catalog_provenance = ghcn.ghcn_index(day)
    window = spec["end"] - spec["start"] + 1
    nearby = []
    for (identifier, element), (first, last) in spans.items():
        # Catalog screen: the inventory must overlap the window by at least MIN_YEARS.
        if element != spec["element"] or min(last, spec["end"]) - max(first, spec["start"]) + 1 < MIN_YEARS:
            continue
        station = stations[identifier]
        distance = distance_km(lat, lon, station["latitude"], station["longitude"])
        if distance <= RADIUS_KM:
            nearby.append({**station, "distance_km": distance})
    nearby.sort(key=lambda row: (row["distance_km"], row["id"]))
    shown = nearby[:shown_limit]

    def fetch(station):
        try:
            only_days = None
            if spec.get("storm_days"):
                from disaster_data import cyclone_footprint_days
                only_days, _ = cyclone_footprint_days(station["latitude"], station["longitude"])
            raw, provenance = ghcn.station_text(station["id"], day)
            return ghcn_annual_maxima(raw, station["id"], spec["element"], spec["start"], spec["end"], spec.get("max_valid"), only_days), provenance
        except (OSError, ValueError) as exc:
            return exc
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(fetch, shown))
    rows = []
    for station, outcome in zip(shown, outcomes):
        row = _row(station)
        if isinstance(outcome, Exception):
            row.update(status="source_unavailable", counted_years=None, annual_maxima=[], points=[],
                       highest=None, median=None, reason=f"NOAA daily record could not be read ({type(outcome).__name__}).")
        else:
            (years, rejected), provenance = outcome
            row.update(source=provenance, period={"start": spec["start"], "end": spec["end"]},
                       excluded_years=[y["year"] for y in years if not y["counted"]])
            if "max_valid" in spec:
                row["rejected_implausible_days"] = rejected
            if spec.get("storm_days"):
                row["storm_years"] = sum(1 for y in years if y.get("storm_days"))
                row["storm_years_missing_readings"] = sum(1 for y in years if y.get("storm_days_missing"))
            _finish(row, [y["maximum"] for y in years if y["counted"]])
        rows.append(row)
    return rows, len(nearby), catalog_provenance, {"start": spec["start"], "end": spec["end"], "window_years": window}


def _download(url, limit=4_000_000, attempts=3):
    """GET with a size limit; network errors are retried (USGS/NWPS fail intermittently)."""
    last = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.5 * attempt)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "NDApp/0.3 station-records research"})
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(limit + 1)
            break
        except OSError as exc:
            last = exc
    else:
        raise last
    if len(raw) > limit:
        raise OSError("Provider response exceeds size limit")
    return raw.decode("utf-8"), {"url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "fetched_at": datetime.now(timezone.utc).isoformat()}


def _rdb(text):
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 2:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[:1] + lines[2:])), delimiter="\t")
    return list(reader)


@lru_cache(maxsize=32)
def nwps_gauges(lat, lon, day):
    """NWS river gauges (NWPS) in a box around the point; only those with flood categories."""
    delta_lat = (RADIUS_KM + 2) / 111.0
    delta_lon = delta_lat / max(0.05, math.cos(math.radians(lat)))
    query = urlencode({"bbox.xmin": f"{lon - delta_lon:.4f}", "bbox.ymin": f"{lat - delta_lat:.4f}",
                       "bbox.xmax": f"{lon + delta_lon:.4f}", "bbox.ymax": f"{lat + delta_lat:.4f}", "srid": "EPSG_4326"})
    text, provenance = _download(f"{NWPS_URL}?{query}", 20_000_000)
    gauges = []
    for gauge in json.loads(text).get("gauges", []):
        category = ((gauge.get("status") or {}).get("observed") or {}).get("floodCategory")
        if category == "not_defined" or not re.fullmatch(r"[A-Z0-9]{3,8}", gauge.get("lid", "")):
            continue
        try:
            gauges.append({"lid": gauge["lid"], "name": gauge.get("name") or gauge["lid"],
                           "latitude": float(gauge["latitude"]), "longitude": float(gauge["longitude"])})
        except (KeyError, TypeError, ValueError):
            continue
    return gauges, provenance


@lru_cache(maxsize=64)
def nwps_gauge(lid, day):
    """Flood stages (ft), linked USGS site and NWS historic crests for one gauge."""
    text, provenance = _download(f"{NWPS_URL}/{lid}")
    detail = json.loads(text)
    flood = detail.get("flood") or {}
    stages = {}
    for level in FLOOD_LEVELS:
        stage = ((flood.get("categories") or {}).get(level) or {}).get("stage")
        if isinstance(stage, (int, float)) and math.isfinite(stage) and stage > 0:
            stages[level] = float(stage)
    crests = [(c.get("occurredTime", "")[:10], c.get("stage")) for c in (flood.get("crests") or {}).get("historic") or []
              if isinstance(c.get("stage"), (int, float))]
    usgs = detail.get("usgsId") or ""
    return {"stages": stages, "units": flood.get("stageUnits") or "ft", "usgs": usgs if re.fullmatch(r"[0-9]{8,15}", usgs) else None,
            "crests": crests}, provenance


@lru_cache(maxsize=32)
def usgs_peaks(site_no, day):
    query = urlencode({"site_no": site_no, "agency_cd": "USGS", "format": "rdb"})
    text, provenance = _download(f"{USGS_PEAK_URL}?{query}")
    return text, provenance


def annual_stages(text, site_no):
    """Highest river level (ft) per water year (Oct–Sep) from the USGS peak file.

    Uses the larger of the peak-flow gage height and the separate maximum gage
    height (ag_gage_ht) when USGS lists one. Historic peaks (peak code 7) and
    readings at a different site or datum (gage-height codes 3 and 6) are left out.
    """
    by_year, datum_excluded, historic = {}, 0, 0
    for row in _rdb(text):
        if row.get("site_no") != site_no:
            raise ValueError("USGS peak record site does not match request")
        if "7" in {code.strip() for code in (row.get("peak_cd") or "").split(",")}:
            historic += 1
            continue
        for date_key, height_key, code_key in (("peak_dt", "gage_ht", "gage_ht_cd"), ("ag_dt", "ag_gage_ht", "ag_gage_ht_cd")):
            match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", row.get(date_key) or "")
            try:
                stage = float(row.get(height_key) or "nan")
            except ValueError:
                continue
            if not match or not math.isfinite(stage):
                continue
            codes = {code.strip() for code in (row.get(code_key) or "").split(",") if code.strip()}
            if codes & {"3", "6"}:
                datum_excluded += 1
                continue
            year, month = int(match[1]), int(match[2])
            water_year = year + 1 if month >= 10 else year
            if water_year not in by_year or stage > by_year[water_year]["stage"]:
                by_year[water_year] = {"stage": stage, "date": row[date_key]}
    return by_year, datum_excluded, historic


def datum_offset(by_year, crests):
    """Largest stage difference (ft) between NWS historic crests and USGS peaks on the same date."""
    usgs_by_date = {entry["date"]: entry["stage"] for entry in by_year.values()}
    differences = [abs(stage - usgs_by_date[day]) for day, stage in crests if day in usgs_by_date]
    return max(differences) if differences else None


def _flood_rows(lat, lon, shown_limit):
    """NWS gauges with flood stages; each gauge's yearly highest river level from USGS."""
    day = int(datetime.now(timezone.utc).timestamp() // 86400)
    gauges, list_provenance = nwps_gauges(round(lat, 2), round(lon, 2), day)
    nearby = sorted(({**g, "distance_km": distance_km(lat, lon, g["latitude"], g["longitude"])} for g in gauges),
                    key=lambda row: (row["distance_km"], row["lid"]))
    nearby = [row for row in nearby if row["distance_km"] <= RADIUS_KM]
    candidates = nearby[:min(len(nearby), shown_limit * 2)]  # some lack a minor flood stage or USGS link

    def fetch(gauge):
        try:
            detail, _ = nwps_gauge(gauge["lid"], day)
            if "minor" not in detail["stages"] or not detail["usgs"]:
                return detail, None
            text, provenance = usgs_peaks(detail["usgs"], day)
            return detail, (annual_stages(text, detail["usgs"]), provenance)
        except (OSError, ValueError) as exc:
            return exc
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(fetch, candidates))
    rows = []
    for gauge, outcome in zip(candidates, outcomes):
        if len(rows) >= shown_limit:
            break
        row = {"id": gauge["lid"], "name": gauge["name"], "latitude": gauge["latitude"], "longitude": gauge["longitude"],
               "distance_km": gauge["distance_km"], "url": f"https://water.noaa.gov/gauges/{gauge['lid']}"}
        if isinstance(outcome, Exception):
            row.update(status="source_unavailable", counted_years=None, annual_maxima=[], points=[], highest=None, median=None,
                       reason=f"NWS or USGS gauge record could not be read ({type(outcome).__name__}).")
            rows.append(row)
            continue
        detail, peaks = outcome
        if peaks is None:
            continue  # no minor flood stage or no linked USGS record: not listed
        (by_year, datum_excluded, historic), provenance = peaks
        years = sorted(by_year)
        if not years:
            continue  # e.g. reservoirs: no river-level history to compare with flood stages
        offset = datum_offset(by_year, detail["crests"])
        row.update(name=f"{gauge['name']} (USGS {detail['usgs']})", flood_stages=detail["stages"], stage_units=detail["units"],
                   source=provenance, period={"start": years[0], "end": years[-1]} if years else None,
                   datum_excluded_readings=datum_excluded, excluded_historic_peaks=historic,
                   datum_check_ft=None if offset is None else round(offset, 2),
                   # NWS and USGS should report the same level for the same flood; a larger gap
                   # suggests different reference heights for part of the record.
                   datum_warning=offset is not None and offset > DATUM_TOLERANCE_FT)
        _finish(row, [by_year[year]["stage"] for year in years])
        rows.append(row)
    return rows, len(nearby), [list_provenance], None


def station_records(kind, lat, lon, requested):
    """Nearest stations' own annual-maximum records for one kind, capped at MAX_STATIONS."""
    if kind not in KINDS:
        raise ValueError("kind must be rainfall, wind or flood.")
    coordinates(lat, lon)
    requested = _requested(requested)
    shown_limit = min(requested, MAX_STATIONS)
    if kind == "flood":
        rows, within, provenance, period = _flood_rows(lat, lon, shown_limit)
    else:
        rows, within, provenance, period = _ghcn_rows(kind, lat, lon, shown_limit)
    spec = KINDS[kind]
    year_rule = ("One highest river level per water year (October–September) from the gauge's USGS peak record; historic peaks and readings at a different site or datum are left out."
                 if kind == "flood" else
                 f"A calendar year counts when at least {round(MIN_DAY_FRACTION * 100)}% of its days have a reading and every tropical-storm day at the station has one. Years with no storm count as 0."
                 if kind == "wind" else
                 f"A calendar year counts when at least {round(MIN_DAY_FRACTION * 100)}% of its days have a valid, unflagged reading.")
    notes = ["Each row is one monitor's own record. Rows are not averaged or combined, and they do not change the map's curve or trigger.",
             f"At most {MAX_STATIONS} stations within {RADIUS_KM} km are listed. Stations with fewer than {MIN_YEARS} counted years still show a %, labelled as an unreliable short record: each year is a large step and a 0% is likely even for events that do occur.",
             "Frequency = 100 × counted years whose maximum is at or above the threshold / counted years. Observed history, not a calibrated future probability."]
    if kind == "rainfall":
        notes.append("Calendar-day totals (observing-day boundaries vary), not NOAA Atlas 14's 24-hour design depths; a storm split across two days is undercounted.")
    elif kind == "wind":
        notes.append("Like the main Wind curve, only tropical cyclones count: a day is used only when a storm's recorded 34 kn wind area (NOAA HURDAT2) covered the station. Thunderstorms and fronts are excluded.")
        notes.append("Each station's fastest 2-minute sustained wind that day (close to the 1-minute sustained wind in the main curve, usually a few percent lower). Gusts are not used.")
        notes.append("Instruments often fail in strong storms. A year with a storm day but no reading is left out rather than guessed, so the strongest storms can be under-represented.")
    else:
        notes.append("Each row is an NWS river gauge with official flood stages (action, minor, moderate, major). The share is of water years whose highest river level reached that gauge's own stage for the chosen flood level.")
        notes.append("River levels come from the linked USGS annual peak record (the higher of the peak-flow and maximum gage heights). Readings at a different site or datum and historic peaks outside the systematic record are left out.")
        notes.append("This is flooding near the gauge, not water depth at the selected point. Dams, levees and channel changes over the decades can change how often a gauge reaches flood stage.")
    return {"kind": kind, "latitude": lat, "longitude": lon, "metric": spec["metric"], "unit": spec["unit"],
            "source_name": spec["source_name"], "period": period, "year_rule": year_rule,
            "requested_stations": requested, "station_limit": MAX_STATIONS, "stations_within_radius": within,
            "radius_km": RADIUS_KM, "minimum_years": MIN_YEARS, "stations": rows,
            "provenance": {"sources": provenance}, "notes": notes}
