"""Versioned research API, sharing providers with the interactive map."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import http.client
import math
from urllib.parse import parse_qs
from address_lookup import geocode_address, normalize_address

from custom_trigger import custom_trigger_level, custom_trigger_note
from area_hazard import SITE_CLASSES, area_disaster, validate_area
from disaster_data import disaster_at
from hazard_engine import coordinates
from monitoring_stations import nearest_hazard_stations, nearest_station, nearest_stations, station_count
from fire_history import nearest_fire
from fire_proximity import MAX_DISTANCE_KM, distance_curve, fire_proximity
from risk_context import (active_weather_alerts, elevation_context, flood_zone_context,
                          national_risk_index, recent_earthquakes)

VERSION = "1.15.0"
# Hazards the public API offers. Wildfire uses the nearby reported-fire method (fire size in acres
# within a radius, share of years), the same one the map's trigger controls use. The older wildfire
# models (FSim flame length, fireline intensity, potential intensity) are not offered.
PERILS = {"auto", "earthquake", "flood", "wildfire", "cyclone", "rainfall", "heat"}
STATION_PERILS = {"earthquake", "flood", "wildfire", "cyclone", "rainfall"}
FIRE_RADII_KM = (5, 10, 20)
DEFAULT_FIRE_RADIUS_KM = 10
ASSESSMENT_HAZARDS = {"flood", "earthquake", "wildfire"}
# Failures a provider can raise on malformed, truncated or unreachable upstream data. Each means
# "no answer" (502), never a substituted estimate. http.client.IncompleteRead is not an OSError.
PROVIDER_ERRORS = (OSError, ValueError, LookupError, TypeError, AttributeError, http.client.HTTPException)
CONTEXT_PROVIDERS = {
    "weather-alerts": (active_weather_alerts, "Agency context snapshot; not an annual probability, property loss estimate or safety determination."),
    "flood-zone-context": (flood_zone_context, "Agency context snapshot; not an annual probability, property loss estimate or safety determination."),
    "elevation-context": (elevation_context, "Ground elevation context; not flood depth, first-floor height or a probability."),
    "risk-index": (national_risk_index, "FEMA census-tract community index; relative rankings, not property-level probability or loss."),
}
PREFIX = "/api/v1"


class InvalidRequest(ValueError):
    pass


class AddressResolutionError(ValueError):
    def __init__(self, lookup):
        self.lookup = lookup
        self.code = "address_ambiguous" if lookup["status"] == "ambiguous" else "address_not_found"
        super().__init__(lookup["message"])


def envelope(data, request, location=None):
    result = {"api_version": VERSION, "status": data.get("status", "available"), "data": data,
            "meta": {"generated_at": datetime.now(timezone.utc).isoformat(), "request": request,
                     "purpose": "Research estimates; not a binding insurance quote or live forecast."}}
    if location is not None:
        result["meta"]["location"] = location
    return result


def error_response(status, code, message):
    result = envelope({}, {})
    result.update(status="error", data=None, error={"code": code, "message": message})
    return status, result


def parameters(raw_query, allowed, required=()):
    try:
        query = parse_qs(raw_query, keep_blank_values=True, max_num_fields=20)
    except ValueError as exc:
        raise InvalidRequest("Too many query parameters.") from exc
    unknown = set(query) - set(allowed)
    if unknown:
        raise InvalidRequest("Unknown query parameter(s): " + ", ".join(sorted(unknown)))
    if any(len(values) != 1 or not values[0].strip() for values in query.values()):
        raise InvalidRequest("Supply each parameter once, with a non-empty value.")
    if set(required) - set(query):
        raise InvalidRequest("Required query parameters: " + ", ".join(required))
    return {key: values[0].strip() for key, values in query.items()}


def number(query, key):
    try:
        value = float(query[key])
        if not math.isfinite(value):
            raise ValueError()
        return value
    except (KeyError, ValueError, OverflowError) as exc:
        raise InvalidRequest(f"{key} must be a finite number.") from exc


def point_parameters(query):
    lat, lon = number(query, "lat"), number(query, "lon")
    try:
        coordinates(lat, lon)
    except ValueError as exc:
        raise InvalidRequest("lat must be between -90 and 90; lon between -180 and 180.") from exc
    return lat, lon


def resolve_location(query):
    if "address" in query:
        if "lat" in query or "lon" in query:
            raise InvalidRequest("Use either address or lat and lon, not both.")
        try:
            normalize_address(query["address"])
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from exc
        lookup = geocode_address(query["address"])
        if lookup["status"] != "matched":
            raise AddressResolutionError(lookup)
        return lookup["latitude"], lookup["longitude"], lookup
    if "lat" not in query or "lon" not in query:
        raise InvalidRequest("Supply a full US street address using address, or both lat and lon.")
    lat, lon = point_parameters(query)
    return lat, lon, None


def payout_parameter(query):
    try:
        payout = Decimal(query["payout_usd"])
        if (not payout.is_finite() or payout < 0 or payout > Decimal("1000000000000")
                or payout != payout.quantize(Decimal("0.01"))):
            raise ValueError()
        return payout
    except (InvalidOperation, ValueError) as exc:
        raise InvalidRequest("payout_usd must be between 0 and 1,000,000,000,000 USD, with at most two decimal places.") from exc


def threshold_parameter(query):
    if "threshold" not in query:
        return None
    value = number(query, "threshold")
    if value < 0:
        raise InvalidRequest("threshold must be a non-negative number in the hazard's units.")
    return value


def money(probability, payout):
    return float((Decimal(str(probability)) / 100 * payout).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def custom_trigger(rows, threshold, payout, unit, extrapolate=True):
    """Estimate a caller-chosen threshold from the source rows, exactly as the map does."""
    levels = [{"severity": row["severity"], **row["frequency_pct"]} for row in rows]
    level = custom_trigger_level(levels, threshold, extrapolate)
    if "error" in level:
        return {"status": "unavailable", "threshold": threshold, "reason": level["error"]}
    return {"status": "available", "threshold": threshold, "estimate": level["method"] != "source",
            "method": level["method"], "direction": level["direction"], "bound": level["bound"],
            "basis_severities": level["basis"],
            "reach_segment_widths": None if level["reach"] is None else round(level["reach"], 4),
            "frequency_pct": {"minimum": level["minimum"], "maximum": level["maximum"]},
            "expected_annual_contribution_usd": {"minimum": money(level["minimum"], payout), "maximum": money(level["maximum"], payout)},
            "note": custom_trigger_note(level, unit)}


def comparison(curve, payout, threshold=None):
    """Mirror the map's exact source-level grouping; never fill missing levels.

    A caller-chosen threshold is returned separately as custom_trigger, labeled as an
    estimate; the comparison rows remain source levels only.
    """
    if curve.get("annual_probability_available") is False:
        result = {**curve, "status": "unavailable", "payout_usd": float(payout), "comparisons": [],
                "frequency_basis": "unavailable", "calculation": "Unavailable: source supplies no annual probabilities.",
                "assumptions": [curve["trigger_unavailable_reason"]],
                "reason": curve.get("reason") or curve["trigger_unavailable_reason"]}
        if threshold is not None:
            result["custom_trigger"] = {"status": "unavailable", "threshold": threshold, "reason": result["reason"]}
        return result
    groups = {}
    if curve.get("status") == "available":
        for point in curve.get("points", []):
            severity, frequency = point.get("severity"), point.get("frequency_pct")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (severity, frequency)) or severity < 0 or not 0 <= frequency <= 100:
                continue
            values = groups.setdefault(severity, [])
            values.append(frequency)
    rows = []
    for severity, values in sorted(groups.items()):
        low, high = min(values), max(values)
        rows.append({"severity": severity, "frequency_pct": {"minimum": low, "maximum": high},
                     "expected_annual_contribution_usd": {"minimum": money(low, payout), "maximum": money(high, payout)}})
    result = {**curve, "payout_usd": float(payout), "comparisons": rows,
              "frequency_basis": {"cyclone": "historical_annual_frequency", "heat": "historical_annual_frequency", "wildfire": "historical_annual_frequency"}.get(curve["peril"], "modeled_annual_probability"),
              "calculation": "(annual exceedance percentage / 100) * fixed payout",
              "assumptions": ["One fixed payout in a year when the trigger is met or exceeded; fees and pricing adjustments excluded.",
                              "Rows are alternative choices, not amounts to add together; contributions rounded to USD cents.",
                              "Only supported source levels; duplicate rounded levels retain a frequency range.",
                              "Wind (cyclone API key) covers tropical cyclones only and reports observed historical frequencies, not calibrated future probabilities. Zero observed years is not zero future risk."]}
    if not rows:
        result.update(status="unavailable", reason=curve.get("reason") or "No supported trigger levels at this location.")
    if curve["peril"] == "heat":
        result["assumptions"].append("Heat contributions use 1991–2020 station-proxy frequencies among complete years. They are historical-frequency illustrations, not forward-looking prices or construction exposure limits.")
    if curve["peril"] == "wildfire":
        result["assumptions"].append(
            f"Wildfire trigger: a reported wildfire of at least the chosen size (acres) within {curve.get('radius_km')} km in a calendar year. "
            "The percentage is the observed share of archive years that had one, not a Poisson rate. "
            "Sizes never reported within the radius are not offered; zero observed years is not zero future risk. "
            "Historical illustration, not a forward-looking price or a property loss probability.")
    if threshold is not None:
        observed = curve["peril"] == "wildfire"
        result["custom_trigger"] = custom_trigger(rows, threshold, payout, curve.get("unit"), extrapolate=not observed)
        if observed and result["custom_trigger"]["status"] == "available" and result["custom_trigger"]["estimate"]:
            result["assumptions"].append(
                "Wildfire custom_trigger is interpolated between observed sizes (log-probability) and never extrapolated: "
                "above the largest observed size it is an upper bound, below the smallest a lower bound.")
        elif result["custom_trigger"]["status"] == "available" and result["custom_trigger"]["estimate"]:
            result["assumptions"].append(
                "custom_trigger is estimated from the source levels (log-probability interpolation between levels; "
                "exponential-tail extrapolation beyond them). It is not a source value; extrapolations are highly uncertain.")
    return result


def fire_radius(query):
    if "radius_km" not in query:
        return DEFAULT_FIRE_RADIUS_KM
    try:
        radius = float(query["radius_km"])
    except ValueError:
        radius = None
    if radius is None or not math.isfinite(radius) or not 0 < radius <= MAX_DISTANCE_KM:
        raise InvalidRequest(f"radius_km must be greater than 0 and at most {MAX_DISTANCE_KM} km for wildfire.")
    return int(radius) if radius.is_integer() else radius


def wildfire_curve(lat, lon, radius_km):
    """Nearby reported-fire trigger curve: fire size (acres) vs share of years with such a fire."""
    proximity = fire_proximity(lat, lon)
    result = {"peril": "wildfire", "status": "unavailable", "radius_km": radius_km,
              "metric": f"Reported wildfire size within {radius_km} km", "unit": "acres",
              "frequency_label": "Share of years with at least one such fire (%)",
              "frequency_basis": "historical_annual_frequency",
              "source_name": proximity.get("source_name"), "source_url": proximity.get("source_url"),
              "points": [], "details": [],
              "notes": ["Each point: the share of archive years with at least one reported wildfire of at least that many acres within the radius.",
                        "Sizes never reported within the radius are left out, not given 0%; zero observed years is not zero future risk.",
                        "Historical reported fires, not a forecast, burn probability for this property or a loss estimate. Full detail: /api/v1/fire-proximity."]}
    baseline = proximity.get("baseline") or {}
    sized = (proximity.get("size_exceedance") or {}).get("sized_fires")
    curve = (distance_curve(sized["rows"], baseline["year_count"], radius_km)
             if proximity.get("status") == "available" and sized and baseline.get("year_count") else None)
    if curve is None:
        result["reason"] = proximity.get("reason") or "Nearby-fire records are unavailable for this location."
        return result
    result["record_period"] = {"start_year": baseline.get("start_year"), "end_year": baseline.get("end_year"),
                               "years": curve.get("total_years")}
    result["details"] = [["Radius", f"{radius_km} km"], ["Reported fires within radius", curve.get("fire_count")],
                         ["Largest reported fire (acres)", curve.get("largest_reported_acres")],
                         ["Record period", f"{baseline.get('start_year')}-{baseline.get('end_year')}"]]
    result["points"] = [{"severity": point["min_acres"], "frequency_pct": point["empirical_years_pct"], "status": "Observed"}
                        for point in curve.get("points", []) if point.get("empirical_years_pct", 0) > 0]
    if result["points"]:
        result["status"] = "available"
    else:
        result["reason"] = (f"No reported wildfire within {radius_km} km in the record period. "
                            "Zero observed years does not mean zero future risk.")
    return result


def dispatch(path, raw_query):
    """Return (HTTP status, JSON payload). All inputs validate before provider calls."""
    try:
        if path == PREFIX + "/health":
            parameters(raw_query, ())
            return 200, envelope({"status": "available", "service": "NDApp research API",
                                  "provider_health_checked": False}, {})
        relative = path.removeprefix(PREFIX + "/").split("/")
        resource = relative[0]
        if resource == "geocode" and len(relative) == 1:
            query = parameters(raw_query, ("address",), ("address",))
            try:
                address = normalize_address(query["address"])
            except ValueError as exc:
                raise InvalidRequest(str(exc)) from exc
            lookup = geocode_address(query["address"])
            result = {**lookup, "match_status": lookup["status"],
                      "status": "available" if lookup["status"] == "matched" else "unavailable"}
            return 200, envelope(result, {"address": address})
        if resource in CONTEXT_PROVIDERS and len(relative) == 1:
            query = parameters(raw_query, ("lat", "lon", "address"))
            lat, lon, location = resolve_location(query)
            request = {"lat": lat, "lon": lon}
            if location:
                request["address"] = location["normalized_address"]
            provider, purpose = CONTEXT_PROVIDERS[resource]
            payload = envelope(provider(lat, lon), request, location)
            payload["meta"]["purpose"] = purpose
            return 200, payload
        if resource == "assessment" and len(relative) == 1:
            query = parameters(raw_query, ("lat", "lon", "address", "hazards"))
            hazards = [item.strip() for item in query.get("hazards", "flood,earthquake,wildfire").split(",")]
            if not hazards or len(set(hazards)) != len(hazards) or not set(hazards) <= ASSESSMENT_HAZARDS:
                raise InvalidRequest("hazards must be a comma-separated list of distinct values from flood, earthquake, wildfire.")
            if "address" in query:
                if "lat" in query or "lon" in query:
                    raise InvalidRequest("Use either address or lat and lon, not both.")
                try:
                    normalize_address(query["address"])
                except ValueError as exc:
                    raise InvalidRequest(str(exc)) from exc
                lat = lon = None
                request = {"address": query["address"], "hazards": hazards}
            else:
                if "lat" not in query or "lon" not in query:
                    raise InvalidRequest("Supply a full US street address using address, or both lat and lon.")
                lat, lon = point_parameters(query)
                request = {"lat": lat, "lon": lon, "hazards": hazards}
            try:
                from risk_agent import web_assessment
            except ImportError:
                return error_response(502, "provider_unavailable",
                                      "The assessment agent is not installed on this server (requirements-agent.txt).")
            result = web_assessment(hazards, lat=lat, lon=lon, address=query.get("address"))
            payload = envelope(result, request)
            payload["meta"]["purpose"] = ("Evidence screening gathered by the NDApp agent over MCP; not a warning, "
                                          "quote, safety determination or loss estimate.")
            return 200, payload
        if resource == "satellite-fires" and len(relative) == 1:
            query = parameters(raw_query, ("lat", "lon", "address", "radius_km", "window"))
            radius = number(query, "radius_km") if "radius_km" in query else 25
            if not 1 <= radius <= 100:
                raise InvalidRequest("radius_km must be between 1 and 100.")
            window = query.get("window", "24h")
            if window not in ("24h", "48h", "7d"):
                raise InvalidRequest("window must be 24h, 48h or 7d.")
            lat, lon, location = resolve_location(query)
            request = {"lat": lat, "lon": lon, "radius_km": radius, "window": window}
            if location:
                request["address"] = location["normalized_address"]
            from satellite_fire import satellite_fire_detections
            payload = envelope(satellite_fire_detections(lat, lon, radius, window), request, location)
            payload["meta"]["purpose"] = ("Satellite thermal-anomaly observations; not confirmed fires, perimeters, "
                                          "fire intensity, probabilities or a safety determination.")
            return 200, payload
        if resource == "recent-earthquakes" and len(relative) == 1:
            query = parameters(raw_query, ("lat", "lon", "address", "radius_km", "days", "min_magnitude"))
            radius = number(query, "radius_km") if "radius_km" in query else 100
            days = number(query, "days") if "days" in query else 365
            magnitude = number(query, "min_magnitude") if "min_magnitude" in query else 2.5
            if not (1 <= radius <= 300 and 1 <= days <= 3650 and days == int(days) and 0 <= magnitude <= 9.5):
                raise InvalidRequest("radius_km must be 1-300, days an integer 1-3650 and min_magnitude 0-9.5.")
            lat, lon, location = resolve_location(query)
            request = {"lat": lat, "lon": lon, "radius_km": radius, "days": int(days), "min_magnitude": magnitude}
            if location:
                request["address"] = location["normalized_address"]
            payload = envelope(recent_earthquakes(lat, lon, radius, int(days), magnitude), request, location)
            payload["meta"]["purpose"] = "Historical catalog snapshot; not a shaking probability, hazard curve or loss estimate."
            return 200, payload
        if resource == "fire-proximity" and len(relative) == 1:
            query = parameters(raw_query, ("lat", "lon", "address"))
            lat, lon, location = resolve_location(query)
            request = {"lat": lat, "lon": lon, "radii_km": [5, 10, 20]}
            if location:
                request["address"] = location["normalized_address"]
            payload = envelope(fire_proximity(lat, lon), request, location)
            payload["meta"]["purpose"] = "Historical annual reported-fire frequencies; not calibrated future probabilities or property damage estimates."
            return 200, payload
        if resource == "nearest-fire" and len(relative) == 1:
            query = parameters(raw_query, ("lat", "lon", "address"))
            lat, lon, location = resolve_location(query)
            request = {"lat": lat, "lon": lon, "time_window": "all_available_history"}
            if location:
                request["address"] = location["normalized_address"]
            payload = envelope(nearest_fire(lat, lon), request, location)
            payload["meta"]["purpose"] = "Reported historical wildfire context; source coverage and location precision vary."
            return 200, payload
        if resource == "monitoring-stations" and len(relative) == 1:
            query = parameters(raw_query, ("peril", "lat", "lon", "address", "n", "network"))
            if "peril" in query and "n" in query:
                if "network" in query:
                    raise InvalidRequest("Use peril with n for hazard stations, or network=awc_metar for weather stations, not both.")
                peril = query["peril"]
                if peril not in STATION_PERILS:
                    raise InvalidRequest("peril must be flood, earthquake, wildfire, cyclone or rainfall.")
                try:
                    n = station_count(query["n"])
                except ValueError as exc:
                    raise InvalidRequest(str(exc)) from exc
                lat, lon, location = resolve_location(query)
                request = {"peril": peril, "n": n, "lat": lat, "lon": lon}
                if location:
                    request["address"] = location["normalized_address"]
                return 200, envelope(nearest_hazard_stations(peril, lat, lon, n), request, location)
            if "peril" not in query or "network" in query:
                if "peril" in query:
                    raise InvalidRequest("Weather-station search uses network=awc_metar, independent of peril. Omit peril.")
                if query.get("network", "awc_metar") != "awc_metar":
                    raise InvalidRequest("network must be awc_metar.")
                try:
                    n = station_count(query.get("n", "5"))
                except ValueError as exc:
                    raise InvalidRequest(str(exc)) from exc
                lat, lon, location = resolve_location(query)
                request = {"network": "awc_metar", "n": n, "lat": lat, "lon": lon}
                if location:
                    request["address"] = location["normalized_address"]
                return 200, envelope(nearest_stations(lat, lon, n), request, location)
            # Preserve existing peril-only clients and the single-station route.
            peril = query["peril"]
            if peril not in STATION_PERILS:
                raise InvalidRequest("peril must be flood, earthquake, wildfire, cyclone or rainfall.")
            lat, lon, location = resolve_location(query)
            request = {"peril": peril, "lat": lat, "lon": lon}
            if location:
                request["address"] = location["normalized_address"]
            return 200, envelope(nearest_station(peril, lat, lon), request, location)
        if resource not in {"hazards", "areas", "trigger-comparisons"} or len(relative) != 2:
            return error_response(404, "not_found", "Unknown v1 endpoint. See /api/docs.")
        peril = relative[1]
        if peril not in PERILS:
            raise InvalidRequest("Unknown hazard. Use flood, earthquake, wildfire, cyclone, rainfall, heat or auto.")
        if resource == "areas":
            keys = ("south", "west", "north", "east")
            query = parameters(raw_query, (*keys, "site_class", "radius_km"), keys)
            bounds = [number(query, key) for key in keys]
            site_class = query.get("site_class", "BC")
            if peril == "wildfire":
                south, west, north, east = bounds
                if not (-90 <= south < north <= 90 and -180 <= west < east <= 180) or site_class not in SITE_CLASSES:
                    raise InvalidRequest("Use ordered south < north, west < east, valid coordinates and site_class.")
                radius = fire_radius(query)
                lat, lon = (south + north) / 2, (west + east) / 2
                result = wildfire_curve(lat, lon, radius)
                result["notes"].insert(0, f"Area request: wildfire uses the area's center ({lat:.5f}, {lon:.5f}), as the map does.")
                result["area"] = {"bounds": dict(zip(keys, bounds)), "centroid": {"latitude": lat, "longitude": lon},
                                  "sample_count": 1, "aggregation": "center_point",
                                  "method": "Nearby reported-fire frequency at the area's center, as the map does.",
                                  "samples": [{"latitude": lat, "longitude": lon}]}
                request = dict(zip(keys, bounds), peril=peril, site_class=site_class, radius_km=radius)
                return 200, envelope(result, request)
            if "radius_km" in query:
                raise InvalidRequest("radius_km applies to wildfire only.")
            try:
                validate_area(*bounds, peril, site_class)
            except ValueError as exc:
                raise InvalidRequest("Use ordered south < north, west < east, valid coordinates and site_class.") from exc
            request = dict(zip(keys, bounds), peril=peril, site_class=site_class)
            return 200, envelope(area_disaster(*bounds, peril, site_class), request)
        required = ("payout_usd",) if resource == "trigger-comparisons" else ()
        allowed = ("lat", "lon", "address", "site_class", "radius_km") + (("payout_usd", "threshold") if required else ())
        query = parameters(raw_query, allowed, required)
        if "radius_km" in query and peril != "wildfire":
            raise InvalidRequest("radius_km applies to wildfire only.")
        radius = fire_radius(query) if peril == "wildfire" else None
        site_class = query.get("site_class", "BC")
        if site_class not in SITE_CLASSES:
            raise InvalidRequest("site_class must be A, B, BC, C, CD, D, DE or E.")
        payout = payout_parameter(query) if resource == "trigger-comparisons" else None
        threshold = threshold_parameter(query)
        lat, lon, location = resolve_location(query)
        request = {"peril": peril, "lat": lat, "lon": lon, "site_class": site_class}
        if location:
            request["address"] = location["normalized_address"]
        if payout is not None:
            request["payout_usd"] = float(payout)
        if radius is not None:
            request["radius_km"] = radius
        if threshold is not None:
            request["threshold"] = threshold
        if peril == "wildfire":
            curve = wildfire_curve(lat, lon, radius)
        elif peril == "auto":
            from hazard_relevance import most_relevant
            curve = most_relevant(lat, lon, site_class)
        else:
            curve = disaster_at(peril, lat, lon, site_class)
        result = comparison(curve, payout, threshold) if payout is not None else curve
        return 200, envelope(result, request, location)
    except InvalidRequest as exc:
        return error_response(400, "invalid_request", str(exc))
    except AddressResolutionError as exc:
        status, result = error_response(422, exc.code, str(exc))
        result["error"]["details"] = exc.lookup
        return status, result
    except PROVIDER_ERRORS as exc:
        print(f"v1 provider failure: {type(exc).__name__}")
        return error_response(502, "provider_unavailable", "The data provider could not complete this lookup. Retry later; no estimate was substituted.")
