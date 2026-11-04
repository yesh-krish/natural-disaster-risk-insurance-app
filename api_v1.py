"""Versioned research API, sharing providers with the interactive map."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
from urllib.parse import parse_qs
from address_lookup import geocode_address, normalize_address

from area_hazard import PERILS, SITE_CLASSES, area_disaster, validate_area
from disaster_data import disaster_at
from hazard_engine import coordinates
from monitoring_stations import nearest_station, nearest_stations, station_count

VERSION = "1.4.0"
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


def comparison(curve, payout):
    """Mirror the map's exact source-level grouping; never fill missing levels."""
    if curve.get("annual_probability_available") is False:
        return {**curve, "status": "unavailable", "payout_usd": float(payout), "comparisons": [],
                "frequency_basis": "unavailable", "calculation": "Unavailable: source supplies no annual probabilities.",
                "assumptions": [curve["trigger_unavailable_reason"]],
                "reason": curve.get("reason") or curve["trigger_unavailable_reason"]}
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
        def contribution(probability):
            return float((Decimal(str(probability)) / 100 * payout).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        rows.append({"severity": severity, "frequency_pct": {"minimum": low, "maximum": high},
                     "expected_annual_contribution_usd": {"minimum": contribution(low), "maximum": contribution(high)}})
    result = {**curve, "payout_usd": float(payout), "comparisons": rows,
              "frequency_basis": {"potential_fire_intensity": "historical_potential_condition_frequency", "cyclone": "historical_annual_frequency", "heat": "historical_annual_frequency", "wildfire": "research_estimate"}.get(curve["peril"], "modeled_annual_probability"),
              "calculation": "(annual exceedance percentage / 100) * fixed payout",
              "assumptions": ["One fixed payout in a year when the trigger is met or exceeded; fees and pricing adjustments excluded.",
                              "Rows are alternative choices, not amounts to add together; contributions rounded to USD cents.",
                              "Only supported source levels; duplicate rounded levels retain a frequency range.",
                              "Wind (cyclone API key) covers tropical cyclones only and reports observed historical frequencies, not calibrated future probabilities. Zero observed years is not zero future risk."]}
    if not rows:
        result.update(status="unavailable", reason=curve.get("reason") or "No supported trigger levels at this location.")
    if curve["peril"] == "heat":
        result["assumptions"].append("Heat contributions use 1991–2020 station-proxy frequencies among complete years. They are historical-frequency illustrations, not forward-looking prices or construction exposure limits.")
    if curve["peril"] == "potential_fire_intensity":
        result["assumptions"].append("This payout illustration triggers on modeled potential conditions, whether or not a fire occurs. It is not wildfire loss pricing or an actual fire-arrival probability.")
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
        if resource == "monitoring-stations" and len(relative) == 1:
            query = parameters(raw_query, ("peril", "lat", "lon", "address", "n", "network"))
            if "peril" not in query or "n" in query or "network" in query:
                if "peril" in query:
                    raise InvalidRequest("Multi-station search uses network=awc_metar, independent of peril. Omit peril.")
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
            if peril not in PERILS - {"auto", "heat", "potential_fire_intensity"}:
                raise InvalidRequest("peril must be flood, earthquake, wildfire, fireline_intensity, cyclone or rainfall.")
            lat, lon, location = resolve_location(query)
            request = {"peril": peril, "lat": lat, "lon": lon}
            if location:
                request["address"] = location["normalized_address"]
            return 200, envelope(nearest_station(peril, lat, lon), request, location)
        if resource not in {"hazards", "areas", "trigger-comparisons"} or len(relative) != 2:
            return error_response(404, "not_found", "Unknown v1 endpoint. See /api/docs.")
        peril = relative[1]
        if peril not in PERILS:
            raise InvalidRequest("Unknown hazard. Use flood, earthquake, wildfire, fireline_intensity, potential_fire_intensity, cyclone, rainfall, heat or auto.")
        if resource == "areas":
            keys = ("south", "west", "north", "east")
            query = parameters(raw_query, (*keys, "site_class"), keys)
            bounds = [number(query, key) for key in keys]
            site_class = query.get("site_class", "BC")
            try:
                validate_area(*bounds, peril, site_class)
            except ValueError as exc:
                raise InvalidRequest("Use ordered south < north, west < east, valid coordinates and site_class.") from exc
            request = dict(zip(keys, bounds), peril=peril, site_class=site_class)
            return 200, envelope(area_disaster(*bounds, peril, site_class), request)
        required = ("payout_usd",) if resource == "trigger-comparisons" else ()
        query = parameters(raw_query, ("lat", "lon", "address", "payout_usd", "site_class") if required else ("lat", "lon", "address", "site_class"), required)
        site_class = query.get("site_class", "BC")
        if site_class not in SITE_CLASSES:
            raise InvalidRequest("site_class must be A, B, BC, C, CD, D, DE or E.")
        payout = payout_parameter(query) if resource == "trigger-comparisons" else None
        lat, lon, location = resolve_location(query)
        request = {"peril": peril, "lat": lat, "lon": lon, "site_class": site_class}
        if location:
            request["address"] = location["normalized_address"]
        if payout is not None:
            request["payout_usd"] = float(payout)
        if peril == "auto":
            from hazard_relevance import most_relevant
            curve = most_relevant(lat, lon, site_class)
        else:
            curve = disaster_at(peril, lat, lon, site_class)
        result = comparison(curve, payout) if payout is not None else curve
        return 200, envelope(result, request, location)
    except InvalidRequest as exc:
        return error_response(400, "invalid_request", str(exc))
    except AddressResolutionError as exc:
        status, result = error_response(422, exc.code, str(exc))
        result["error"]["details"] = exc.lookup
        return status, result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"v1 provider failure: {type(exc).__name__}")
        return error_response(502, "provider_unavailable", "The data provider could not complete this lookup. Retry later; no estimate was substituted.")
